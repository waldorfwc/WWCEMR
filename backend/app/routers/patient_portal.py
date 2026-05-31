"""Patient portal — self-service sign-in + dashboard.

Lives alongside patient_surgery.py:
  - patient_surgery.py = one-shot magic-link flows (slot picker)
  - patient_portal.py  = durable, session-based portal

Auth flow (2-step):
  1. POST /login   (DOB + last4) -> sends SMS, returns challenge_token
  2. POST /verify  (challenge_token + code) -> JWT
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import PatientAuthAttempt, Surgery
from app.services import patient_portal_auth as auth

router = APIRouter(prefix="/patient/portal", tags=["patient-portal"])

LOCKOUT_FAILS = 3
LOCKOUT_WINDOW_MIN = 15


def _is_locked_out(db: Session, surgery_id: str) -> bool:
    cutoff = datetime.utcnow() - timedelta(minutes=LOCKOUT_WINDOW_MIN)
    fails = (db.query(PatientAuthAttempt)
                .filter(PatientAuthAttempt.surgery_id == surgery_id,
                         PatientAuthAttempt.success.is_(False),
                         PatientAuthAttempt.attempted_at >= cutoff)
                .count())
    return fails >= LOCKOUT_FAILS


def _log_attempt(db: Session, surgery_id, *, success: bool,
                  request: Request) -> None:
    ip = request.client.host if request.client else None
    db.add(PatientAuthAttempt(surgery_id=surgery_id, success=success,
                                ip_address=ip))
    db.commit()


def _normalize_last4(raw: str) -> str:
    return "".join(c for c in (raw or "") if c.isdigit())


def _match_surgery(
    db: Session,
    dob_str: str,
    last4_in: str,
) -> tuple[Surgery | None, Surgery | None]:
    """Find the Surgery row that matches both DOB and last-4 of phone.

    Returns (matched, attempted):
    - matched: the Surgery if both DOB and last4 match; else None.
    - attempted: the first Surgery whose DOB matched (even if last4 failed);
      used by the caller to log a failed attempt so lockout works.

    Validation errors (bad date format / bad last4 length) raise HTTPException
    422 directly.
    """
    try:
        dob = datetime.strptime(dob_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise HTTPException(status_code=422,
                             detail="Please enter your date of birth as YYYY-MM-DD")
    if len(last4_in) != 4:
        raise HTTPException(status_code=422,
                             detail="Please enter the last 4 digits of your phone number")

    first_dob_match: Surgery | None = None
    for s in db.query(Surgery).filter(Surgery.dob == dob).all():
        if first_dob_match is None:
            first_dob_match = s
        on_file = s.cell_phone or s.phone or ""
        digits = _normalize_last4(on_file)
        if len(digits) >= 4 and digits[-4:] == last4_in:
            return s, s

    # DOB matched at least one row but last4 didn't (or nothing matched at all)
    return None, first_dob_match


# ─── /login ─────────────────────────────────────────────────────

class LoginPayload(BaseModel):
    dob: str            # YYYY-MM-DD
    phone_last4: str    # 4 digits


@router.post("/login")
def login(payload: LoginPayload, request: Request,
            db: Session = Depends(get_db)):
    """Step 1 of sign-in. Generic error on no match (don't leak which field failed)."""
    last4 = _normalize_last4(payload.phone_last4)
    matched, attempted = _match_surgery(db, payload.dob, last4)

    if matched is None:
        # Log a failed attempt against the DOB-matched surgery (if any) so
        # that repeated wrong-last4 guesses for a known patient accumulate
        # toward lockout. If nothing matched at all, we can't track by surgery.
        if attempted is not None:
            if _is_locked_out(db, attempted.id):
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many failed attempts. Please wait "
                           f"{LOCKOUT_WINDOW_MIN} minutes or call our "
                           f"office at 240-252-2140.",
                )
            _log_attempt(db, attempted.id, success=False, request=request)
        raise HTTPException(status_code=404,
                             detail="No surgery matches that information")

    if _is_locked_out(db, matched.id):
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Please wait "
                   f"{LOCKOUT_WINDOW_MIN} minutes or call our "
                   f"office at 240-252-2140.",
        )

    challenge_token, _code = auth.issue_challenge(db, matched)
    _log_attempt(db, matched.id, success=True, request=request)
    return {"challenge_token": challenge_token}
