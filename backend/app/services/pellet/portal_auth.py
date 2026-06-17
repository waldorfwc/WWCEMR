"""Pellet portal auth: DOB+last4 login → SMS code challenge → JWT.
Mirrors surgery patient_portal_auth.py but keys off PelletPatient and a
`ppv` (pellet-portal-version) revocation claim."""
from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, timedelta

from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.utils.dt import now_utc_naive
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletPortalAuthAttempt

_ALGO = "HS256"
_TOKEN_TTL_DAYS = 30
_CODE_TTL_MIN = 10
_MAX_ATTEMPTS = 5     # burn the challenge after this many wrong codes


def _secret() -> str:
    return settings.secret_key


def issue_portal_token(p: PelletPatient) -> str:
    payload = {
        "pellet_patient_id": str(p.id),
        "ppv": int(p.portal_token_version or 0),
        "exp": now_utc_naive() + timedelta(days=_TOKEN_TTL_DAYS),
        "scope": "pellet_portal",
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGO)


def decode_portal_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALGO])
    except JWTError:
        return None


def compute_token_exp(p: PelletPatient) -> datetime:
    return now_utc_naive() + timedelta(days=_TOKEN_TTL_DAYS)


def _send_sms(phone: str, body: str) -> None:
    from app.services.checklist_notifications import send_sms
    send_sms(phone, body)


def _digits(s: str | None) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def match_patient(db: Session, dob: date, last4: str) -> PelletPatient | None:
    # Normalize last4 to exactly 4 digits — never interpolate raw input into a
    # SQL LIKE (a '%'/'_' would broaden the match on this unauthenticated
    # endpoint). Compare digit-normalized phones in Python so formatted numbers
    # like "(301) 555-1234" still match. Mirrors the surgery portal.
    last4 = _digits(last4)[-4:]
    if len(last4) != 4:
        return None
    rows = db.query(PelletPatient).filter(PelletPatient.patient_dob == dob).all()
    matches = [r for r in rows if _digits(r.patient_phone).endswith(last4)]
    return matches[0] if len(matches) == 1 else None


def issue_challenge(db: Session, p: PelletPatient, purpose: str = "login") -> str:
    code = f"{secrets.randbelow(900000) + 100000}"
    ct = secrets.token_urlsafe(24)
    db.add(PelletPortalAuthAttempt(
        pellet_patient_id=p.id, challenge_token=ct,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        purpose=purpose, created_at=now_utc_naive(),
        expires_at=now_utc_naive() + timedelta(minutes=_CODE_TTL_MIN)))
    db.commit()
    _send_sms(p.patient_phone, f"Your Waldorf Women's Care pellet portal code is {code}")
    return ct


def verify_code(db: Session, challenge_token: str, code: str) -> PelletPatient | None:
    att = (db.query(PelletPortalAuthAttempt)
             .filter(PelletPortalAuthAttempt.challenge_token == challenge_token,
                     PelletPortalAuthAttempt.consumed_at.is_(None))
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
    return db.query(PelletPatient).filter(PelletPatient.id == att.pellet_patient_id).first()
