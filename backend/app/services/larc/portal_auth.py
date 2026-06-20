"""LARC portal auth: DOB+last4 login → SMS code challenge → JWT.
Cloned from app/services/pellet/portal_auth.py but keys off a
LarcAssignment and an `lpv` (larc-portal-version) revocation claim.
OTP/hash/JWT mechanics are identical to the pellet portal."""
from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, timedelta

from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.utils.dt import now_utc_naive
from app.models.larc import LarcAssignment, LarcPortalAuthAttempt

_ALGO = "HS256"
_TOKEN_TTL_DAYS = 30
_CODE_TTL_MIN = 10
_MAX_ATTEMPTS = 5     # burn the challenge after this many wrong codes


def _secret() -> str:
    return settings.secret_key


def issue_portal_token(assignment: LarcAssignment, *, viewer: str | None = None,
                       ttl_minutes: int | None = None) -> str:
    exp = (now_utc_naive() + timedelta(minutes=ttl_minutes)) if ttl_minutes \
          else (now_utc_naive() + timedelta(days=_TOKEN_TTL_DAYS))
    payload = {
        "sub": str(assignment.id),
        "lpv": int(assignment.portal_token_version or 0),
        "exp": exp,
        "scope": "larc_portal",
    }
    if viewer:
        payload["viewer"] = viewer
    return jwt.encode(payload, _secret(), algorithm=_ALGO)


def decode_portal_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALGO])
    except JWTError:
        return None


def compute_token_exp(assignment: LarcAssignment) -> datetime:
    return now_utc_naive() + timedelta(days=_TOKEN_TTL_DAYS)


def _send_sms(phone: str, body: str) -> None:
    from app.services.checklist_notifications import send_sms
    send_sms(phone, body)


def _digits(s: str | None) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def match_assignment(db: Session, dob: date, last4: str) -> LarcAssignment | None:
    # Normalize last4 to exactly 4 digits — never interpolate raw input into a
    # SQL LIKE (a '%'/'_' would broaden the match on this unauthenticated
    # endpoint). Compare digit-normalized phones in Python so formatted numbers
    # like "(301) 555-1234" still match. Returns the most-recent active request.
    last4 = _digits(last4)[-4:]
    if len(last4) != 4:
        return None
    rows = (db.query(LarcAssignment)
              .filter(LarcAssignment.is_active.is_(True),
                      LarcAssignment.patient_dob == dob)
              .order_by(LarcAssignment.created_at.desc())
              .all())
    for r in rows:
        if _digits(r.patient_cell).endswith(last4):
            return r
    return None


def issue_challenge(db: Session, assignment: LarcAssignment, purpose: str = "login") -> str:
    code = f"{secrets.randbelow(900000) + 100000}"
    ct = secrets.token_urlsafe(24)
    db.add(LarcPortalAuthAttempt(
        assignment_id=assignment.id, challenge_token=ct,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        purpose=purpose, created_at=now_utc_naive(),
        expires_at=now_utc_naive() + timedelta(minutes=_CODE_TTL_MIN)))
    db.commit()
    _send_sms(assignment.patient_cell, f"Your Waldorf Women's Care LARC portal code is {code}")
    return ct


def verify_code(db: Session, challenge_token: str, code: str) -> LarcAssignment | None:
    att = (db.query(LarcPortalAuthAttempt)
             .filter(LarcPortalAuthAttempt.challenge_token == challenge_token,
                     LarcPortalAuthAttempt.consumed_at.is_(None))
             .first())
    if att is None or (att.expires_at and att.expires_at < now_utc_naive()):
        return None
    if hashlib.sha256(code.encode()).hexdigest() != att.code_hash:
        # Wrong code: count the attempt and burn the challenge after too many,
        # so a 6-digit code can't be brute-forced within its 10-min TTL.
        att.attempts = (att.attempts or 0) + 1
        if att.attempts >= _MAX_ATTEMPTS:
            att.consumed_at = now_utc_naive()
        db.commit()
        return None
    att.consumed_at = now_utc_naive()
    db.commit()
    return db.query(LarcAssignment).filter(LarcAssignment.id == att.assignment_id).first()
