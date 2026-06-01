"""Portal auth helpers — challenge codes (issue/verify) + JWT (issue/verify).

The challenge lifecycle:
  issue_challenge(db, surgery) -> (challenge_token, plaintext_code)
    - persists hashed code with 5-min TTL
    - sends SMS via the existing send_sms infrastructure
  verify_code(db, challenge_token, code) -> Optional[surgery_id]
    - returns the surgery_id on success, None otherwise
    - 3 wrong codes kills the challenge

JWT TTL is pegged to `surgery.scheduled_date + 30 days` per the P1 spec.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from typing import Optional

import bcrypt as _bcrypt  # passlib's bcrypt backend is broken against bcrypt>=4; use raw package
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.models.patient_portal import PatientPortalAuthCode
from app.models.surgery import Surgery
from app.services.checklist_notifications import send_sms

PORTAL_TOKEN_AUDIENCE = "wwc:patient-portal"
CODE_TTL_MINUTES = 5
CODE_MAX_FAILS = 3
PURPOSE_COPY = {
    "login":   ("WWC: Your portal sign-in code is {code}. "
                  "Expires in {ttl} minutes."),
    "payment": ("WWC: Code to authorize your payment: {code}. "
                  "Expires in {ttl} minutes. If you didn't request this, ignore."),
}


def _now() -> datetime:
    return datetime.utcnow()


def _generate_code() -> str:
    """6-digit numeric code, leading zeros preserved."""
    return f"{secrets.randbelow(10**6):06d}"


def issue_challenge(db: Session, surgery: Surgery,
                      purpose: str = "login") -> tuple[str, str]:
    """Generate a code, persist its hash, SMS the plaintext to the surgery's
    cell_phone. Returns (challenge_token, plaintext_code).

    `purpose` picks the SMS copy — "login" (default, sign-in) or "payment"
    (step-up before charge). The lifecycle is identical; only the body
    text changes. The PatientPortalAuthCode row does NOT store purpose —
    the caller is responsible for invoking verify_code from the matching
    endpoint context (and route-level checks prevent cross-purpose abuse).

    Precondition: surgery.cell_phone or surgery.phone must be non-empty.
    If both are blank, the SMS silently no-ops and the patient cannot
    complete the action. Endpoints must validate before calling.
    """
    code = _generate_code()
    challenge_token = secrets.token_urlsafe(32)
    row = PatientPortalAuthCode(
        surgery_id=surgery.id,
        challenge_token=challenge_token,
        code_hash=_bcrypt.hashpw(code.encode(), _bcrypt.gensalt()).decode(),
        expires_at=_now() + timedelta(minutes=CODE_TTL_MINUTES),
        sent_to_phone=surgery.cell_phone or surgery.phone or "",
    )
    db.add(row); db.commit()
    phone = row.sent_to_phone
    template = PURPOSE_COPY.get(purpose, PURPOSE_COPY["login"])
    body = template.format(code=code, ttl=CODE_TTL_MINUTES)
    send_sms(phone, body)
    return challenge_token, code


def verify_code(db: Session, challenge_token: str, code: str) -> Optional[str]:
    """Return surgery_id on success, None on any failure. Replay-safe
    (used_at is stamped on the first successful check)."""
    # TODO: tighten with SELECT ... FOR UPDATE to close the TOCTOU window
    # between the read below and the used_at/fail_count write. Two
    # concurrent requests with the right code could both succeed today.
    row = (db.query(PatientPortalAuthCode)
              .filter(PatientPortalAuthCode.challenge_token == challenge_token)
              .first())
    if row is None or row.used_at is not None:
        return None
    if _now() > row.expires_at:
        return None
    if row.fail_count >= CODE_MAX_FAILS:
        return None
    if not _bcrypt.checkpw(code.encode(), row.code_hash.encode()):
        row.fail_count += 1
        db.commit()
        return None
    row.used_at = _now()
    db.commit()
    return row.surgery_id


def compute_token_exp(surgery: Surgery,
                        now: Optional[datetime] = None) -> datetime:
    """JWT exp = max(today, surgery.scheduled_date) + 30 days.

    If scheduled_date is None, defaults to today + 30 days.
    The 'now' argument is for tests; production callers omit it.
    """
    now = now or _now()
    base = now.date()
    if surgery.scheduled_date and surgery.scheduled_date > base:
        base = surgery.scheduled_date
    exp_date = base + timedelta(days=30)
    return datetime.combine(exp_date, datetime.min.time())


def issue_portal_token(surgery: Surgery, *,
                          viewer: Optional[str] = None,
                          ttl_minutes: Optional[int] = None) -> str:
    """Sign a portal JWT. Default TTL is scheduled_date + 30 days. Pass
    ttl_minutes for short-lived tokens (e.g. coordinator preview = 60).
    Pass viewer='staff:<email>' so the read-only gate kicks in for non-GET
    requests."""
    if ttl_minutes is not None:
        exp = datetime.utcnow() + timedelta(minutes=ttl_minutes)
    else:
        exp = compute_token_exp(surgery)
    payload = {
        "sub": str(surgery.id),
        "aud": PORTAL_TOKEN_AUDIENCE,
        "exp": exp,
    }
    if viewer:
        payload["viewer"] = viewer
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def verify_portal_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"],
                              audience=PORTAL_TOKEN_AUDIENCE)
        return payload.get("sub")
    except JWTError:
        return None


def decode_portal_token(token: str) -> Optional[dict]:
    """Return the full JWT payload dict (or None if invalid). Use when you
    need the viewer claim; otherwise prefer verify_portal_token (returns
    just sub)."""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"],
                            audience=PORTAL_TOKEN_AUDIENCE)
    except JWTError:
        return None
