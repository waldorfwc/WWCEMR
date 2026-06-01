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

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import PatientAuthAttempt, Surgery
from app.services import patient_portal_auth as auth
from app.services.surgery_klara_drafter import FACILITY_SHORT

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


# ─── /verify ────────────────────────────────────────────────────

class VerifyPayload(BaseModel):
    challenge_token: str
    code: str


@router.post("/verify")
def verify(payload: VerifyPayload, db: Session = Depends(get_db)):
    """Step 2 of sign-in. Returns JWT on success.

    All failure modes (unknown challenge, expired, wrong code, too many
    fails) collapse to a single 401 with a generic message — same
    no-leak posture as /login.
    """
    code = "".join(c for c in (payload.code or "") if c.isdigit())
    if len(code) != 6:
        raise HTTPException(status_code=401, detail="Invalid code")
    surgery_id = auth.verify_code(db, payload.challenge_token, code)
    if surgery_id is None:
        raise HTTPException(status_code=401, detail="Invalid code")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=401, detail="Invalid code")
    token = auth.issue_portal_token(s)
    return {
        "token": token,
        "surgery_id": str(s.id),
        "expires_at": auth.compute_token_exp(s).isoformat(),
    }


# ─── Auth dependency ────────────────────────────────────────────

def require_portal_token(
    surgery_id: str,
    authorization: str = Header(default=""),
) -> str:
    """Validate Bearer token; ensure it's for THIS surgery_id."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1].strip()
    sub = auth.verify_portal_token(token)
    if sub is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    if sub != surgery_id:
        raise HTTPException(status_code=403, detail="Wrong surgery")
    return sub


# ─── /{surgery_id}/dashboard ────────────────────────────────────

def _money(x) -> float | None:
    if x is None:
        return None
    return float(x)


def _payment_milestone(surgery: Surgery) -> dict:
    """Sum SurgeryPayment(status='paid') and compare to pt responsibility."""
    paid = 0.0
    for p in (surgery.payments or []):
        if p.status == "paid":
            paid += float(p.amount_paid or 0)
    due = float(surgery.patient_responsibility or 0)
    if due <= 0:
        return {"key": "payment", "label": "Patient responsibility",
                "status": "not_required", "paid": paid, "due": due}
    status = "done" if paid >= due else ("in_progress" if paid > 0 else "todo")
    return {"key": "payment", "label": "Patient responsibility paid",
            "status": status, "paid": paid, "due": due}


def _schedule_milestone(surgery: Surgery) -> dict:
    return {
        "key": "schedule",
        "label": "Surgery date selected",
        "status": "done" if surgery.scheduled_date else "todo",
        "value": surgery.scheduled_date.isoformat() if surgery.scheduled_date else None,
    }


def _consent_milestone(surgery: Surgery) -> dict:
    envs = list(surgery.consent_envelopes or [])
    if not envs:
        return {"key": "consent", "label": "Consent forms signed",
                "status": "todo"}
    if all(e.status == "signed" for e in envs):
        return {"key": "consent", "label": "Consent forms signed",
                "status": "done", "count": len(envs)}
    return {"key": "consent", "label": "Consent forms signed",
            "status": "in_progress",
            "signed": sum(1 for e in envs if e.status == "signed"),
            "total": len(envs)}


def _self_report_milestone(surgery, *, attr, label, key) -> dict:
    val = getattr(surgery, attr, False)
    return {"key": key, "label": label,
            "status": "done" if val else "todo"}


def _next_action(milestones: list[dict]) -> dict | None:
    """First non-done milestone wins; map to a CTA stub."""
    priority = ["payment", "schedule", "consent",
                 "fmla", "labs", "hospital_preop"]
    for key in priority:
        m = next((x for x in milestones if x["key"] == key), None)
        if m and m.get("status") in ("todo", "in_progress"):
            return {"key": key, "label": m["label"]}
    return None


@router.get("/{surgery_id}/dashboard")
def dashboard(surgery_id: str, db: Session = Depends(get_db),
                _: str = Depends(require_portal_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    first_proc = (s.procedures or [{}])[0]
    summary = {
        "procedure":     first_proc.get("description") or first_proc.get("name") or "",
        "surgeon":       s.surgeon_primary or "",
        "surgery_date":  s.scheduled_date.isoformat() if s.scheduled_date else None,
        "surgery_time":  s.scheduled_start_time.strftime("%H:%M")
                            if s.scheduled_start_time else None,
        "facility":      FACILITY_SHORT.get(s.selected_facility or "",
                                             s.selected_facility or ""),
        "patient_responsibility": _money(s.patient_responsibility),
    }
    milestones = [
        _payment_milestone(s),
        _schedule_milestone(s),
        _consent_milestone(s),
        _self_report_milestone(s, attr="hospital_preop_self_reported",
                                  label="Hospital pre-op call",
                                  key="hospital_preop"),
        _self_report_milestone(s, attr="labs_self_reported",
                                  label="Labs completed",
                                  key="labs"),
    ]
    # FMLA is opt-in for patients who need leave from work — only surface
    # the row when fmla_status is populated. NULL means "patient did not
    # request FMLA," which is the common case; showing the row would imply
    # an unmet task that doesn't actually exist.
    if (getattr(s, "fmla_status", None) or "").strip():
        milestones.append({
            "key": "fmla",
            "label": "FMLA submitted",
            "status": s.fmla_status,
        })
    return {
        "surgery": summary,
        "milestones": milestones,
        "next_action": _next_action(milestones),
    }


# ─── /{surgery_id}/payments ─────────────────────────────────────

from app.models.stripe_payment import SurgeryPayment


@router.get("/{surgery_id}/payments")
def portal_payments(surgery_id: str, db: Session = Depends(get_db),
                      _: str = Depends(require_portal_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    due  = float(s.patient_responsibility or 0)
    paid = float(s.amount_paid or 0)
    balance = max(0.0, due - paid)
    history = []
    for p in (s.payments or []):
        history.append({
            "id":        str(p.id),
            "status":    p.status,
            "amount_requested": str(p.amount_requested or 0),
            "amount_paid":      str(p.amount_paid or 0),
            "requested_at":     p.requested_at.isoformat() if p.requested_at else None,
            "paid_at":          p.paid_at.isoformat() if p.paid_at else None,
            "checkout_url":     p.checkout_url,
        })
    return {
        "due":     due,
        "paid":    paid,
        "balance": balance,
        "history": history,
    }
