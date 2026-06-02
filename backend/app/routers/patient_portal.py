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
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
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
    request: Request,
    surgery_id: str,
    authorization: str = Header(default=""),
) -> str:
    """Validate Bearer token; ensure it's for THIS surgery_id. When the
    token's viewer claim is a staff impersonation (starts with 'staff:'),
    reject non-GET requests — coordinators preview, they don't act."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1].strip()
    payload = auth.decode_portal_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    sub = payload.get("sub")
    if sub != surgery_id:
        raise HTTPException(status_code=403, detail="Wrong surgery")
    viewer = payload.get("viewer") or ""
    if viewer.startswith("staff:") and request.method != "GET":
        raise HTTPException(status_code=403,
                              detail="Preview mode is read-only.")
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
        "patient_name":  s.patient_name or "",
        "chart_number":  s.chart_number or "",
        "procedure":     first_proc.get("description") or first_proc.get("name") or "",
        "surgeon":       s.surgeon_primary or "",
        "surgery_date":  s.scheduled_date.isoformat() if s.scheduled_date else None,
        "surgery_time":  s.scheduled_start_time.strftime("%H:%M")
                            if s.scheduled_start_time else None,
        "facility":      FACILITY_SHORT.get(s.selected_facility or "",
                                             s.selected_facility or ""),
        "facility_code": s.selected_facility or "",
        "patient_responsibility": _money(s.patient_responsibility),
        "outstanding_balance":    _money(
            max(0, (s.patient_responsibility or 0) - (s.amount_paid or 0))
        ) if s.patient_responsibility else None,
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


# ─── /{surgery_id}/documents ──────────────────────────────────────

@router.get("/{surgery_id}/documents")
def portal_documents(surgery_id: str, db: Session = Depends(get_db),
                       _: str = Depends(require_portal_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")

    # Consents — only show the ones the patient can actually download
    consents = []
    for env in (s.consent_envelopes or []):
        if (env.status or "") not in ("signed", "completed"):
            continue
        consents.append({
            "envelope_id":    str(env.id),
            "template_name":  env.template.name if env.template else "",
            "status":         env.status,
            "signed_at":      env.signed_at.isoformat() if env.signed_at else None,
        })

    # Receipts — only paid rows
    receipts = []
    for p in (s.payments or []):
        if p.status != "paid":
            continue
        receipts.append({
            "id":         str(p.id),
            "paid_at":    p.paid_at.isoformat() if p.paid_at else None,
            "amount":     str(p.amount_paid or 0),
        })

    # Instructions: structure stays present so the frontend can show both
    # rows. When the procedure has no classification, the whole section is
    # null and the frontend renders the "not available" message.
    if s.procedure_classification:
        instructions = {
            "preop":  {"available": None,   # Lazy: frontend probes on click
                       "kind": "preop"},
            "postop": {"available": None,
                       "kind": "postop"},
        }
    else:
        instructions = None

    # Was a personalized clearance form generated by staff?
    from app.models.surgery import SurgeryFile
    cf = (db.query(SurgeryFile)
            .filter(SurgeryFile.surgery_id == s.id,
                    SurgeryFile.kind == "clearance_form")
            .order_by(SurgeryFile.uploaded_at.desc())
            .first())

    return {
        "instructions": instructions,
        "consents":     consents,
        "receipts":     receipts,
        "clearance": {
            "required":         bool(s.clearance_required),
            "status":           s.clearance_status or "not_required",
            "form_available":   cf is not None,
            "form_filename":    cf.filename if cf else None,
            "form_id":          str(cf.id) if cf else None,
        },
        "labs": {
            "scheduled_date":   (str(s.scheduled_date)
                                  if s.scheduled_date else None),
            "appointment_date":
                str(s.lab_appointment_date) if s.lab_appointment_date else None,
            "reported_at":
                s.lab_appointment_reported_at.isoformat()
                if s.lab_appointment_reported_at else None,
        },
    }


# ─── /{surgery_id}/documents/instructions/{kind} ────────────────

@router.get("/{surgery_id}/documents/instructions/{kind}")
def portal_documents_instructions(
    surgery_id: str,
    kind: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Stream a procedure-specific instructions PDF from GCS.
    kind ∈ {"preop", "postop"}. Returns 404 when the patient's
    procedure_classification has no doc in the library."""
    if kind not in ("preop", "postop"):
        raise HTTPException(status_code=422,
                              detail="kind must be 'preop' or 'postop'")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not s.procedure_classification:
        raise HTTPException(
            status_code=404,
            detail="Instructions for this procedure aren't online yet — "
                   "please call our office at 240-252-2140.",
        )
    from app.services.surgery_documents import fetch_instructions_pdf
    pdf_bytes = fetch_instructions_pdf(s.procedure_classification, kind)
    if pdf_bytes is None:
        raise HTTPException(
            status_code=404,
            detail="Instructions for this procedure aren't online yet — "
                   "please call our office at 240-252-2140.",
        )
    filename = f"{s.procedure_classification}_{kind}_instructions.pdf"
    from fastapi.responses import Response
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── /{surgery_id}/self-report/* ──────────────────────────────────

def _flip_if_unset(surgery: Surgery, flag_attr: str, ts_attr: str) -> None:
    """Idempotent flip: only stamps the first time the flag goes True."""
    if not getattr(surgery, flag_attr, False):
        setattr(surgery, flag_attr, True)
        setattr(surgery, ts_attr, datetime.utcnow())


@router.post("/{surgery_id}/self-report/labs")
def portal_self_report_labs(surgery_id: str,
                                db: Session = Depends(get_db),
                                _: str = Depends(require_portal_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    _flip_if_unset(s, "labs_self_reported", "labs_self_reported_at")
    db.commit()
    return {
        "labs_self_reported": s.labs_self_reported,
        "labs_self_reported_at":
            s.labs_self_reported_at.isoformat()
            if s.labs_self_reported_at else None,
    }


class LabAppointmentDateBody(BaseModel):
    date: Optional[str] = None  # YYYY-MM-DD or null to clear


@router.post("/{surgery_id}/self-report/lab-appointment-date")
def portal_self_report_lab_appt_date(
    surgery_id: str,
    payload: LabAppointmentDateBody,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Patient self-reports the date of their pre-op lab appointment.
    Practice rule: 4–7 days before surgery. The endpoint validates the
    date is in that window so patients don't book labs too early/late."""
    from datetime import datetime as _dt, timedelta as _td
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")

    if payload.date is None or payload.date == "":
        s.lab_appointment_date = None
        s.lab_appointment_reported_at = None
        s.lab_appointment_reported_by = None
    else:
        try:
            d = _dt.strptime(payload.date[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422,
                                detail="date must be YYYY-MM-DD")
        if s.scheduled_date:
            earliest = s.scheduled_date - _td(days=7)
            latest   = s.scheduled_date - _td(days=4)
            if d < earliest or d > latest:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Pick a date between {earliest} and {latest} "
                        f"(4–7 days before your surgery).")
                )
        s.lab_appointment_date = d
        s.lab_appointment_reported_at = _dt.utcnow()
        s.lab_appointment_reported_by = "patient"

    db.commit()
    return {
        "lab_appointment_date":
            str(s.lab_appointment_date) if s.lab_appointment_date else None,
        "lab_appointment_reported_at":
            s.lab_appointment_reported_at.isoformat()
            if s.lab_appointment_reported_at else None,
    }


@router.post("/{surgery_id}/self-report/hospital-preop")
def portal_self_report_hospital_preop(
    surgery_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    _flip_if_unset(s, "hospital_preop_self_reported",
                       "hospital_preop_self_reported_at")
    db.commit()
    return {
        "hospital_preop_self_reported": s.hospital_preop_self_reported,
        "hospital_preop_self_reported_at":
            s.hospital_preop_self_reported_at.isoformat()
            if s.hospital_preop_self_reported_at else None,
    }


# ─── /{surgery_id}/clearance/* ─────────────────────────────────────

@router.get("/{surgery_id}/clearance/generated-form")
def portal_clearance_generated_form(
    surgery_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Download the personalized clearance form that staff generated for
    this patient. Streams the PDF from storage. Falls back to the static
    template endpoint if no per-patient form exists."""
    from app.models.surgery import SurgeryFile
    from app.services.storage import serve_blob, is_legacy_local_path
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")

    f = (db.query(SurgeryFile)
           .filter(SurgeryFile.surgery_id == s.id,
                   SurgeryFile.kind == "clearance_form")
           .order_by(SurgeryFile.uploaded_at.desc())
           .first())
    if f is None:
        raise HTTPException(
            status_code=404,
            detail="No clearance form has been generated yet — please call "
                   "our office at 240-252-2140.")
    if is_legacy_local_path(f.path):
        raise HTTPException(
            status_code=410,
            detail="This file is from before the cloud migration and is "
                   "no longer available.")
    return serve_blob(
        local_path=None,
        gcs_object=f.path,
        media_type=f.mime_type or "application/pdf",
        filename=f.filename,
        disposition="attachment",
    )


@router.get("/{surgery_id}/clearance/template")
def portal_clearance_template(
    surgery_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Stream the blank clearance template PDF from GCS. Gated on
    clearance_required."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not s.clearance_required:
        raise HTTPException(
            status_code=409,
            detail="Clearance isn't required for this surgery.",
        )
    from app.services.surgery_uploads import stream_static_pdf
    pdf_bytes = stream_static_pdf("clearance/template.pdf")
    if pdf_bytes is None:
        raise HTTPException(
            status_code=404,
            detail="The clearance template isn't online yet — please call "
                   "our office at 240-252-2140.",
        )
    from fastapi.responses import Response
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                'attachment; filename="wwc_clearance_template.pdf"',
        },
    )


@router.post("/{surgery_id}/clearance/upload")
async def portal_clearance_upload(
    surgery_id: str,
    file: UploadFile = File(...),
    kind: str = Form("clearance"),
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Accept a multipart upload of the patient's completed clearance form
    or EKG. kind defaults to 'clearance'; pass 'ekg' for EKG uploads."""
    if kind not in ("clearance", "ekg"):
        raise HTTPException(status_code=422,
                              detail="kind must be 'clearance' or 'ekg'")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    contents = await file.read()
    from app.services.surgery_uploads import store_upload, UploadError
    try:
        doc = store_upload(
            db, s, kind=kind,
            filename=file.filename or "upload",
            file_bytes=contents,
            content_type=file.content_type or "application/octet-stream",
            uploaded_by="patient:portal",
        )
    except UploadError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    # Move clearance_status forward if it was "required"; don't downgrade
    # "approved" rows.
    if (s.clearance_status or "") in ("required", "not_required", ""):
        s.clearance_status = "uploaded"
        db.commit()
    return {
        "id":               str(doc.id),
        "kind":             doc.kind,
        "filename":         doc.filename,
        "uploaded_at":      doc.uploaded_at.isoformat(),
        "clearance_status": s.clearance_status,
    }


# ─── /{surgery_id}/fmla/upload ────────────────────────────────────

@router.post("/{surgery_id}/fmla/upload")
async def portal_fmla_upload(
    surgery_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Patient uploads their employer-provided blank FMLA form."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    contents = await file.read()
    from app.services.surgery_uploads import store_upload, UploadError
    try:
        doc = store_upload(
            db, s, kind="fmla_blank",
            filename=file.filename or "fmla.pdf",
            file_bytes=contents,
            content_type=file.content_type or "application/octet-stream",
            uploaded_by="patient:portal",
        )
    except UploadError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    # Both payment and upload are required; paid-first path completes here.
    if s.fmla_fee_paid and not s.fmla_status:
        s.fmla_status = "submitted"
        db.commit()
    return {
        "id":          str(doc.id),
        "kind":        doc.kind,
        "filename":    doc.filename,
        "uploaded_at": doc.uploaded_at.isoformat(),
        "fmla_status": s.fmla_status or "",
    }


# ─── /{surgery_id}/uploads ────────────────────────────────────────

@router.get("/{surgery_id}/uploads")
def portal_uploads(surgery_id: str, db: Session = Depends(get_db),
                       _: str = Depends(require_portal_token)):
    """Return the patient's uploaded documents with fresh 5-minute
    signed-URL downloads."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    from app.services.surgery_uploads import signed_download_url
    docs = []
    for d in (s.documents or []):
        try:
            url = signed_download_url(d, ttl_minutes=5)
        except Exception:
            url = None
        docs.append({
            "id":           str(d.id),
            "kind":         d.kind,
            "filename":     d.filename,
            "uploaded_at":  d.uploaded_at.isoformat() if d.uploaded_at else None,
            "size_bytes":   d.size_bytes,
            "content_type": d.content_type,
            "download_url": url,
        })
    return {"uploads": docs}


# ─── GET /{surgery_id}/fmla ────────────────────────────────────────

@router.get("/{surgery_id}/fmla")
def portal_fmla(surgery_id: str, db: Session = Depends(get_db),
                  _: str = Depends(require_portal_token)):
    """Aggregated FMLA state for the patient's UI: status, fee amount,
    fee_paid flag, and both upload lists (with signed URLs on the
    coordinator-uploaded completed forms only)."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    import os
    from app.services.surgery_uploads import signed_download_url
    fee_cents = int(os.environ.get("FMLA_FEE_CENTS", "2500") or "2500")
    fee = Decimal(fee_cents) / Decimal(100)

    def _doc(d, signed: bool) -> dict:
        url = None
        if signed:
            try:
                url = signed_download_url(d, ttl_minutes=5)
            except Exception:
                url = None
        return {
            "id":           str(d.id),
            "filename":     d.filename,
            "uploaded_at":  d.uploaded_at.isoformat() if d.uploaded_at else None,
            "download_url": url,
        }

    blank_uploads     = [_doc(d, signed=False) for d in s.documents
                            if d.kind == "fmla_blank"]
    completed_uploads = [_doc(d, signed=True)  for d in s.documents
                            if d.kind == "fmla_completed"]

    return {
        "status":            s.fmla_status or "",
        "fee_amount":        f"{fee:.2f}",
        "fee_paid":          bool(s.fmla_fee_paid),
        "fee_paid_at":       s.fmla_fee_paid_at.isoformat() if s.fmla_fee_paid_at else None,
        "blank_uploads":     blank_uploads,
        "completed_uploads": completed_uploads,
    }


# ─── /{surgery_id}/fmla/step-up + /fmla/checkout ────────────────

@router.post("/{surgery_id}/fmla/step-up")
def portal_fmla_step_up(
    surgery_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Send a fresh SMS code so the patient can authorize the FMLA fee
    charge. Caller must POST /fmla/checkout within 5 minutes."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.fmla_fee_paid:
        raise HTTPException(status_code=422,
                              detail="The FMLA fee has already been paid.")
    if not (s.cell_phone or s.phone or "").strip():
        raise HTTPException(status_code=409,
                              detail="No phone on file — call our office at "
                                     "240-252-2140.")
    challenge_token, _code = auth.issue_challenge(db, s, purpose="payment")
    return {"step_up_token": challenge_token}


class FmlaCheckoutPayload(BaseModel):
    step_up_token: str
    code: str


@router.post("/{surgery_id}/fmla/checkout")
def portal_fmla_checkout(
    surgery_id: str,
    payload: FmlaCheckoutPayload,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Verify the SMS code; create a Stripe Checkout session for the
    FMLA processing fee. Returns the URL the browser should visit."""
    code = "".join(c for c in (payload.code or "") if c.isdigit())
    if len(code) != 6:
        raise HTTPException(status_code=401, detail="Invalid code")
    matched_sid = auth.verify_code(db, payload.step_up_token, code)
    if matched_sid is None or matched_sid != surgery_id:
        raise HTTPException(status_code=401, detail="Invalid code")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.fmla_fee_paid:
        raise HTTPException(status_code=422,
                              detail="The FMLA fee has already been paid.")
    import os
    fee_cents = int(os.environ.get("FMLA_FEE_CENTS", "2500") or "2500")
    fee = Decimal(fee_cents) / Decimal(100)
    if not stripe_svc.is_configured():
        raise HTTPException(status_code=503,
                              detail="Payments aren't available right now.")
    try:
        pay = stripe_svc.create_checkout_session(
            db, s, amount=fee,
            description="FMLA processing fee",
            actor="patient:portal:fmla",
            kind="fmla_fee",
        )
    except Exception as e:
        log = logging.getLogger(__name__)
        log.exception("portal FMLA checkout create failed")
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")
    return {"checkout_url": pay.checkout_url, "payment_id": str(pay.id)}


# ─── /{surgery_id}/messages ────────────────────────────────────────

from app.models.surgery_message import SurgeryMessage


def _msg_dict(m: SurgeryMessage) -> dict:
    return {
        "id":           str(m.id),
        "author_kind":  m.author_kind,
        "author_label": "You" if m.author_kind == "patient" else "WWC",
        "body":         m.body,
        "sent_at":      m.sent_at.isoformat() if m.sent_at else None,
    }


class MessagePostPayload(BaseModel):
    body: str


@router.get("/{surgery_id}/messages")
def portal_messages_get(
    surgery_id: str,
    request: Request,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Return the patient's full thread oldest->newest. Marks staff-authored
    messages as read by the patient -- unless the JWT is a preview token
    (#154), in which case the read-state mutation is skipped so the real
    patient's unread state is preserved."""
    # require_portal_token also validates the bearer + surgery match; we
    # call it manually so we can also inspect the viewer claim for the
    # preview-skip decision below.
    require_portal_token(request, surgery_id, authorization)
    token = authorization.split(" ", 1)[1].strip() if " " in authorization else ""
    payload = auth.decode_portal_token(token) or {}
    is_preview = (payload.get("viewer") or "").startswith("staff:")

    msgs = (db.query(SurgeryMessage)
              .filter(SurgeryMessage.surgery_id == surgery_id)
              .order_by(SurgeryMessage.sent_at.asc())
              .all())
    if not is_preview:
        for m in msgs:
            if m.author_kind == "staff" and m.read_by_patient_at is None:
                m.read_by_patient_at = datetime.utcnow()
        db.commit()
    return {"messages": [_msg_dict(m) for m in msgs]}


@router.post("/{surgery_id}/messages")
def portal_messages_post(
    surgery_id: str,
    payload: MessagePostPayload,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    m = SurgeryMessage(
        surgery_id=s.id,
        author_kind="patient",
        body=body,
    )
    db.add(m); db.commit(); db.refresh(m)
    return _msg_dict(m)
