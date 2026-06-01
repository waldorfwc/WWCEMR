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
from decimal import Decimal

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


# ─── /{surgery_id}/payments/step-up + /payments/checkout ────────

import logging
from app.services import stripe_payments as stripe_svc


@router.post("/{surgery_id}/payments/step-up")
def portal_payments_step_up(
    surgery_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Send a fresh SMS code for payment authorization. Caller must POST
    /payments/checkout within 5 minutes with the code."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    due  = s.patient_responsibility or Decimal(0)
    paid = s.amount_paid or Decimal(0)
    if due <= 0 or paid >= due:
        raise HTTPException(status_code=422,
                              detail="No outstanding balance to pay.")
    if not (s.cell_phone or s.phone or "").strip():
        raise HTTPException(status_code=409,
                              detail="No phone on file — call our office at "
                                     "240-252-2140.")
    challenge_token, _code = auth.issue_challenge(db, s, purpose="payment")
    return {"step_up_token": challenge_token}


class CheckoutPayload(BaseModel):
    step_up_token: str
    code: str


@router.post("/{surgery_id}/payments/checkout")
def portal_payments_checkout(
    surgery_id: str,
    payload: CheckoutPayload,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Verify the step-up code; create a Stripe Checkout session for the
    outstanding balance. Returns the URL the browser should visit."""
    code = "".join(c for c in (payload.code or "") if c.isdigit())
    if len(code) != 6:
        raise HTTPException(status_code=401, detail="Invalid code")
    matched_sid = auth.verify_code(db, payload.step_up_token, code)
    if matched_sid is None or matched_sid != surgery_id:
        raise HTTPException(status_code=401, detail="Invalid code")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    due  = s.patient_responsibility or Decimal(0)
    paid = s.amount_paid or Decimal(0)
    amount = max(Decimal(0), due - paid)
    if amount <= 0:
        raise HTTPException(status_code=422,
                              detail="No outstanding balance to pay.")
    if not stripe_svc.is_configured():
        raise HTTPException(status_code=503,
                              detail="Payments aren't available right now.")
    try:
        pay = stripe_svc.create_checkout_session(
            db, s, amount=amount,
            description="Surgery balance (patient self-service)",
            actor="patient:portal",
        )
    except Exception as e:
        log = logging.getLogger(__name__)
        log.exception("portal checkout create failed")
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")
    return {"checkout_url": pay.checkout_url, "payment_id": str(pay.id)}


# ─── /{surgery_id}/slots + /{sid}/slots/{bd}/claim ─────────────

from app.services.surgery_self_schedule import (
    claim_slot_for_patient, SelfScheduleError, schedule_gate_for_surgery,
)


@router.get("/{surgery_id}/slots")
def portal_slots(surgery_id: str, days_ahead: int = 180,
                   db: Session = Depends(get_db),
                   _: str = Depends(require_portal_token)):
    """Available block days for this surgery. When the schedule gate is
    blocked, returns an empty days list with the reason; the frontend
    renders a payment-prompt banner instead of the picker.

    Delegates to patient_surgery.patient_slots for the actual block-day
    enumeration so the portal and magic-link flows can't drift."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    allowed, reason = schedule_gate_for_surgery(s)
    if not allowed:
        return {
            "gate": {"allowed": False, "reason": reason},
            "block_days": [],
        }
    # Direct function call (not via FastAPI deps) to share the magic-link
    # slot logic exactly. _token is unused by the function body.
    from app.routers.patient_surgery import patient_slots as _ms_slots
    raw = _ms_slots(surgery_id, days_ahead=days_ahead, db=db, _token="")
    return {
        "gate": {"allowed": True, "reason": None},
        "block_days": raw.get("days", []),
        "procedure_kind": raw.get("procedure_kind"),
        "duration_minutes": raw.get("duration_minutes"),
    }


class PortalClaimPayload(BaseModel):
    start_time: str  # "HH:MM"


@router.post("/{surgery_id}/slots/{block_day_id}/claim")
def portal_claim_slot(
    surgery_id: str,
    block_day_id: str,
    payload: PortalClaimPayload,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    allowed, reason = schedule_gate_for_surgery(s)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)
    try:
        result = claim_slot_for_patient(
            db, s,
            block_day_id=block_day_id,
            start_time_str=payload.start_time,
            sent_by="patient:portal",
        )
    except SelfScheduleError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"ok": True, **result}


# ─── /{surgery_id}/consent ────────────────────────────────────────

from app.models.surgery import SurgeryConsentEnvelope


def _envelope_dict(env: SurgeryConsentEnvelope) -> dict:
    status = env.status or ""
    return {
        "id":               str(env.id),
        "template_name":    env.template.name if env.template else "",
        "boldsign_envelope_id": env.boldsign_envelope_id,
        "status":           status,
        "sent_at":          env.sent_at.isoformat() if env.sent_at else None,
        "signed_at":        env.signed_at.isoformat() if env.signed_at else None,
        "can_sign":         status in ("sent", "delivered", "pending"),
        "can_download":     status in ("signed", "completed"),
    }


@router.get("/{surgery_id}/consent")
def portal_consent(surgery_id: str, db: Session = Depends(get_db),
                     _: str = Depends(require_portal_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    envs = [_envelope_dict(e) for e in (s.consent_envelopes or [])]
    all_complete = bool(envs) and all(
        (e["status"] in ("signed", "completed")) for e in envs
    )
    return {
        "scheduled_date": s.scheduled_date.isoformat() if s.scheduled_date else None,
        "envelopes": envs,
        "all_complete": all_complete,
        "can_resend": s.scheduled_date is not None,
    }


@router.post("/{surgery_id}/consent/resend")
def portal_consent_resend(surgery_id: str, db: Session = Depends(get_db),
                            _: str = Depends(require_portal_token)):
    """Manual retry of consent envelope creation. Used when auto-send at
    slot-claim time failed (e.g., BoldSign outage). Requires a scheduled
    date — patient must have completed the schedule flow first."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.scheduled_date is None:
        raise HTTPException(
            status_code=409,
            detail="Please pick your surgery date first; consent forms "
                   "are created when you schedule.",
        )
    from app.services.boldsign_envelopes import (
        send_consent_envelopes, BoldSignEnvelopeError,
    )
    try:
        send_consent_envelopes(db, s, sent_by="patient:portal:resend")
    except BoldSignEnvelopeError as e:
        # Service-level rejection (e.g. no matching templates) — surface
        # the message to the patient.
        raise HTTPException(status_code=409, detail=str(e))
    # Re-fetch the consent payload so the frontend has fresh state.
    return portal_consent(surgery_id, db=db, _="ignored")


@router.get("/{surgery_id}/consent/sign-link/{envelope_id}")
def portal_consent_sign_link(
    surgery_id: str,
    envelope_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Return a BoldSign embedded sign URL for the patient role on this
    envelope. Hardcodes signer_email to surgery.email so the endpoint
    cannot be tricked into returning the surgeon's or witness's link."""
    env = (db.query(SurgeryConsentEnvelope)
              .filter(SurgeryConsentEnvelope.id == envelope_id,
                       SurgeryConsentEnvelope.surgery_id == surgery_id)
              .first())
    if env is None:
        raise HTTPException(status_code=404, detail="envelope not found")
    if not env.boldsign_envelope_id:
        raise HTTPException(status_code=409,
                              detail="Envelope was not sent via BoldSign.")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not (s.email or "").strip():
        raise HTTPException(status_code=409,
                              detail="No email on file — call our office.")
    from app.services.boldsign_envelopes import (
        get_embedded_sign_link, BoldSignEnvelopeError,
    )
    try:
        url = get_embedded_sign_link(env.boldsign_envelope_id, s.email)
    except BoldSignEnvelopeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"sign_url": url}


@router.get("/{surgery_id}/consent/signed-pdf/{envelope_id}")
def portal_consent_signed_pdf(
    surgery_id: str,
    envelope_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Stream the signed PDF from BoldSign for download.
    Only available when envelope.status is signed or completed."""
    from fastapi import Response
    env = (db.query(SurgeryConsentEnvelope)
              .filter(SurgeryConsentEnvelope.id == envelope_id,
                       SurgeryConsentEnvelope.surgery_id == surgery_id)
              .first())
    if env is None:
        raise HTTPException(status_code=404, detail="envelope not found")
    if (env.status or "") not in ("signed", "completed"):
        raise HTTPException(
            status_code=409,
            detail="Document is not yet signed by all parties.",
        )
    if not env.boldsign_envelope_id:
        raise HTTPException(status_code=409,
                              detail="Envelope was not sent via BoldSign.")
    from app.services.boldsign_envelopes import (
        download_signed_pdf, BoldSignEnvelopeError,
    )
    try:
        pdf_bytes = download_signed_pdf(env.boldsign_envelope_id)
    except BoldSignEnvelopeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # Use a friendly filename based on the template name.
    label = "consent"
    if env.template and env.template.name:
        # Strip non-alphanum to keep filename safe.
        label = "".join(c if c.isalnum() else "_"
                          for c in env.template.name)[:60].strip("_") or "consent"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{label}.pdf"'},
    )
