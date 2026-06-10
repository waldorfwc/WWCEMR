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
from app.utils.dt import now_utc_naive
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
# Precomputed bcrypt hash for timing-equalization (Fable portal audit
# H3-auth). The plaintext doesn't matter — checkpw against this hash
# takes the same ~100ms a real wrong-code check takes, so the
# expired / used / over-limit / unknown-challenge paths can't be
# distinguished from a wrong-code response by response time.
_DUMMY_HASH = _bcrypt.hashpw(b"never-matches-any-real-code",
                              _bcrypt.gensalt(rounds=12))
PURPOSE_COPY = {
    "login":   ("WWC: Your portal sign-in code is {code}. "
                  "Expires in {ttl} minutes."),
    "payment": ("WWC: Code to authorize your payment: {code}. "
                  "Expires in {ttl} minutes. If you didn't request this, ignore."),
    "review":  ("WWC: Code to confirm you're a patient for your review: "
                  "{code}. Expires in {ttl} minutes."),
}


class SmsSendError(Exception):
    """Raised by issue_challenge when the SMS couldn't be delivered.
    The challenge row is voided before this is raised."""


def _now() -> datetime:
    return now_utc_naive()


def _generate_code() -> str:
    """6-digit numeric code, leading zeros preserved."""
    return f"{secrets.randbelow(10**6):06d}"


def issue_challenge(db: Session, surgery: Surgery,
                      purpose: str = "login") -> str:
    """Generate a code, persist its hash + purpose, SMS the plaintext to
    the surgery's cell_phone. Returns the challenge_token (the plaintext
    code is never returned — it only travels via SMS).

    `purpose` picks the SMS copy AND is persisted on the row, so a code
    issued for purpose="login" cannot be replayed at verify_code(...,
    purpose="payment"). (Fable portal audit C1/C3.)

    Precondition: surgery.cell_phone or surgery.phone must be non-empty.
    If both are blank, the SMS silently no-ops and the patient cannot
    complete the action. Endpoints must validate before calling.
    """
    if purpose not in PURPOSE_COPY:
        raise ValueError(f"unknown purpose: {purpose!r}")
    code = _generate_code()
    challenge_token = secrets.token_urlsafe(32)
    row = PatientPortalAuthCode(
        surgery_id=surgery.id,
        challenge_token=challenge_token,
        code_hash=_bcrypt.hashpw(code.encode(), _bcrypt.gensalt()).decode(),
        expires_at=_now() + timedelta(minutes=CODE_TTL_MINUTES),
        sent_to_phone=surgery.cell_phone or surgery.phone or "",
        purpose=purpose,
    )
    db.add(row); db.commit()
    phone = row.sent_to_phone
    template = PURPOSE_COPY[purpose]
    body = template.format(code=code, ttl=CODE_TTL_MINUTES)
    # send_sms returns None on any failure (no config / bad phone / Twilio
    # error / network exception). If we ignore that, the row commits but
    # no SMS arrives — the patient waits for a code that never comes,
    # then the staff has to manually rescue them. Void the row so the
    # code can't be later guessed against, and raise so the caller can
    # surface a 502 / friendly error. (Fable portal audit H1-auth.)
    try:
        sid = send_sms(phone, body)
    except Exception as exc:
        row.used_at = _now()
        db.commit()
        raise SmsSendError(
            f"SMS delivery failed (Twilio raised): {exc!r}") from exc
    if not sid:
        row.used_at = _now()
        db.commit()
        raise SmsSendError(
            "We couldn't text your code right now. Please try again or "
            "call our office at 240-252-2140.")
    return challenge_token


def verify_code(db: Session, challenge_token: str, code: str,
                  *, purpose: str = "login") -> Optional[str]:
    """Return surgery_id on success, None on any failure. Replay-safe
    (used_at is stamped on the first successful check).

    `purpose` must match the purpose the challenge was issued with —
    otherwise the verify silently fails. Legacy rows with NULL purpose
    are treated as 'login' for back-compat so in-flight challenges
    don't break the moment this deploys.

    Concurrency: row is locked via SELECT ... FOR UPDATE so two
    concurrent right-code requests don't both succeed, and N concurrent
    wrong guesses don't all read fail_count=0 and all write
    fail_count=1 (which made the 3-fail lockout unenforceable and
    online brute force feasible). (Fable portal audit H2-auth.)

    Timing: every failure path runs a bcrypt.checkpw against a dummy
    hash so the response time doesn't leak whether the challenge was
    unknown / expired / used / locked / wrong-purpose vs. wrong-code.
    (Fable portal audit H3-auth.)
    """
    row = (db.query(PatientPortalAuthCode)
              .filter(PatientPortalAuthCode.challenge_token == challenge_token)
              .with_for_update()
              .first())

    def _equalize_timing() -> None:
        """Run bcrypt against a dummy hash so the timing of early-return
        failures matches the live wrong-code path."""
        try:
            _bcrypt.checkpw(code.encode(), _DUMMY_HASH)
        except Exception:
            pass

    if row is None:
        _equalize_timing()
        return None
    if row.used_at is not None:
        _equalize_timing()
        return None
    if _now() > row.expires_at:
        _equalize_timing()
        return None
    if row.fail_count >= CODE_MAX_FAILS:
        _equalize_timing()
        return None
    # Purpose binding (Fable portal audit C1). NULL = legacy row, treat
    # as 'login' for back-compat. Mismatch increments fail_count so a
    # cross-purpose probe burns the challenge.
    row_purpose = row.purpose or "login"
    if row_purpose != purpose:
        row.fail_count += 1
        db.commit()
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
    """JWT exp = max(now, scheduled_date midnight) + 30 days.

    If scheduled_date is None, defaults to now + 30 days.
    The 'now' argument is for tests; production callers omit it.

    Previously this returned `datetime.combine(exp_date, datetime.min.time())`
    — midnight at the START of day 30 — so the real TTL was up to 24h
    short of "+30 days" depending on what time of day the token was
    issued. Switched to a full datetime + timedelta computation so
    "30 days" actually means 30 × 24h. (Fable portal audit H4-auth.)
    """
    now = now or _now()
    if surgery.scheduled_date and surgery.scheduled_date > now.date():
        base = datetime.combine(surgery.scheduled_date, datetime.min.time())
    else:
        base = now
    return base + timedelta(days=30)


def issue_portal_token(surgery: Surgery, *,
                          viewer: Optional[str] = None,
                          ttl_minutes: Optional[int] = None) -> str:
    """Sign a portal JWT. Default TTL is scheduled_date + 30 days. Pass
    ttl_minutes for short-lived tokens (e.g. coordinator preview = 60).
    Pass viewer='staff:<email>' so the read-only gate kicks in for non-GET
    requests.

    Embeds the surgery's current portal_token_version as `ptv`.
    require_portal_token / require_patient_token reject tokens whose
    ptv doesn't match the current row, so cancel_surgery /
    consent_reset can revoke outstanding tokens by bumping the
    version. (Fable portal audit H5-auth.)
    """
    if ttl_minutes is not None:
        exp = now_utc_naive() + timedelta(minutes=ttl_minutes)
    else:
        exp = compute_token_exp(surgery)
    payload = {
        "sub": str(surgery.id),
        "aud": PORTAL_TOKEN_AUDIENCE,
        "exp": exp,
        "iat": now_utc_naive(),
        "ptv": int(getattr(surgery, "portal_token_version", 0) or 0),
    }
    if viewer:
        payload["viewer"] = viewer
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def verify_portal_token(token: str) -> Optional[str]:
    """Return surgery_id (sub) on success. Note: this DOES NOT check
    the per-surgery ptv claim — callers that need revocation should
    use decode_portal_token + load the Surgery row + compare ptv.
    The router-side require_portal_token / require_patient_token
    dependencies do this; raw verify_portal_token is kept for
    callers that don't have DB access (rare).
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"],
                              audience=PORTAL_TOKEN_AUDIENCE)
        return payload.get("sub")
    except JWTError:
        return None


def decode_portal_token(token: str) -> Optional[dict]:
    """Return the full JWT payload dict (or None if invalid). Use when you
    need the viewer/ptv claim; otherwise prefer verify_portal_token
    (returns just sub)."""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"],
                            audience=PORTAL_TOKEN_AUDIENCE)
    except JWTError:
        return None


def bump_portal_token_version(db: Session, surgery: Surgery) -> None:
    """Invalidate every outstanding portal/patient token for this
    surgery. Used by cancel_surgery / consent_reset to ensure a
    cancelled patient can't continue acting via a still-valid JWT.
    Caller is responsible for committing.
    """
    current = int(getattr(surgery, "portal_token_version", 0) or 0)
    surgery.portal_token_version = current + 1
