"""Public patient-facing surgery date-picker API.

These endpoints live at /api/p/surgery/* and do NOT require the staff
session cookie. Instead, the patient soft-authenticates with:

  DOB (full) + last 4 digits of phone number on file

After 3 failed attempts within 15 minutes, the surgery is locked from
patient access until the lockout expires.

Successful auth issues a short-lived JWT (1 hour) keyed to the surgery
ID. The patient uses that token for status / available-slots / pick.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from app.utils.dt import now_utc_naive
from decimal import Decimal
from typing import Optional

import os

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.exc import StaleDataError

from app.config import settings
from app.database import get_db
from app.models.surgery import (
    BlockDay, PatientAuthAttempt, Surgery, SurgeryFile, SurgerySlot,
    SurgeryNote,
)
from app.services.surgery.block_schedule import (
    DURATIONS, can_fit, book_slot, CapacityViolation,
)
from app.services.surgery.slot_conflict import overlapping_slot
from app.services.surgery.blackout_conflict import is_date_blacked_out
from app.services.surgery.settings import cfg

log = logging.getLogger(__name__)

router = APIRouter(prefix="/p/surgery", tags=["patient-surgery"])


# ─── Confirmation email helper ───────────────────────────────────────

def _send_surgery_confirmation_email(db, surgery, slot, sent_by: str) -> None:
    """Soft-fail confirmation email + SMS after a slot is booked."""
    from app.services.patient_email import send_patient_email
    from app.services.patient_sms import send_patient_sms, build_sms_context

    start_time = (slot.start_time.strftime("%H:%M")
                    if slot and slot.start_time else "")
    surgery_date = (surgery.scheduled_date.isoformat()
                      if surgery.scheduled_date else "")
    procedure = ""
    if surgery.procedures:
        procedure = surgery.procedures[0].get("name", "")
    # Email uses the long ISO date + raw HH:MM (lives in a roomier rendering).
    email_ctx = {
        "patient_name": surgery.patient_name,
        "surgery_date": surgery_date,
        "start_time":   start_time,
        "facility":     surgery.selected_facility or "",
        "procedure":    procedure,
    }
    send_patient_email(
        db, kind="surgery_confirmation",
        to_email=surgery.email, context=email_ctx,
        sent_by=sent_by, surgery_id=surgery.id,
        chart_number=surgery.chart_number,
    )
    send_patient_sms(
        db, kind="sms_surgery_confirmation",
        surgery=surgery,
        context=build_sms_context(surgery),
        sent_by=sent_by,
    )


# ─── Token helpers ──────────────────────────────────────────────────

PATIENT_TOKEN_TTL_HOURS = 1
LOCKOUT_FAILS = 3
LOCKOUT_WINDOW_MIN = 15
PATIENT_TOKEN_AUDIENCE = "wwc:patient-surgery"


def _issue_patient_token(surgery_id: str, ptv: int = 0) -> str:
    expire = now_utc_naive() + timedelta(hours=PATIENT_TOKEN_TTL_HOURS)
    return jwt.encode(
        {"sub": str(surgery_id), "aud": PATIENT_TOKEN_AUDIENCE,
         "exp": expire, "iat": now_utc_naive(), "ptv": int(ptv)},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def _decode_patient_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.secret_key,
                             algorithms=[settings.algorithm],
                             audience=PATIENT_TOKEN_AUDIENCE)
    except JWTError:
        return None


def _verify_patient_token(token: str, surgery_id: str) -> bool:
    payload = _decode_patient_token(token)
    if payload is None:
        return False
    return payload.get("sub") == str(surgery_id)


def require_patient_token(surgery_id: str,
                            db: Session = Depends(get_db),
                            authorization: Optional[str] = Header(None)) -> str:
    """Raises 401 if the token doesn't match this surgery.

    Also enforces the per-surgery portal_token_version (`ptv`) claim so
    a cancel_surgery / consent_reset can revoke outstanding magic-link
    tokens. (Fable portal audit H5-auth.)
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing patient token")
    token = authorization[7:]
    payload = _decode_patient_token(token)
    if payload is None or payload.get("sub") != str(surgery_id):
        raise HTTPException(status_code=401, detail="Invalid or expired patient token")
    s_row = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s_row is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    current_ptv = int(getattr(s_row, "portal_token_version", 0) or 0)
    token_ptv = payload.get("ptv")
    if token_ptv is None:
        if current_ptv != 0:
            raise HTTPException(status_code=401, detail="Token revoked")
    elif int(token_ptv) != current_ptv:
        raise HTTPException(status_code=401, detail="Token revoked")
    return token


# ─── Terminal-status guard ──────────────────────────────────────────

TERMINAL_SURGERY_STATUSES = ("cancelled", "completed", "unresponsive")


def _reject_if_terminal(s: Surgery) -> None:
    """Reject (409) any patient self-service write against a terminal-state
    surgery. Mirrors the guard patient_cancel / pick_or_reschedule already
    enforce — endpoints that mutate a closed case (cardiologist, sms-consent,
    upload-fmla) lacked it. (audit #30)"""
    if s.status in TERMINAL_SURGERY_STATUSES:
        raise HTTPException(status_code=409, detail="This surgery is no longer active.")


# ─── Lockout check ──────────────────────────────────────────────────

def _is_locked_out(db: Session, surgery_id: str) -> bool:
    cutoff = now_utc_naive() - timedelta(minutes=LOCKOUT_WINDOW_MIN)
    fails = (db.query(PatientAuthAttempt)
               .filter(PatientAuthAttempt.surgery_id == surgery_id,
                       PatientAuthAttempt.success.is_(False),
                       PatientAuthAttempt.attempted_at >= cutoff)
               .count())
    return fails >= LOCKOUT_FAILS


# ─── Auth ───────────────────────────────────────────────────────────

class AuthPayload(BaseModel):
    dob: str            # YYYY-MM-DD
    phone_last4: str    # 4 digits


@router.post("/{surgery_id}/auth")
def patient_auth(surgery_id: str, payload: AuthPayload,
                  request: Request, db: Session = Depends(get_db)):
    """Soft-auth: DOB + last 4 of phone. 3 failures = 15-min lockout."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        # Don't leak existence — same generic error
        raise HTTPException(status_code=404, detail="No surgery matches that link")

    if _is_locked_out(db, surgery_id):
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Please wait {LOCKOUT_WINDOW_MIN} minutes and try again, "
                   f"or call our office at 240-252-2140."
        )

    # Validate DOB
    try:
        dob_in = datetime.strptime(payload.dob[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        _log_attempt(db, surgery_id, success=False, request=request)
        raise HTTPException(status_code=422, detail="Invalid date of birth — use YYYY-MM-DD")

    last4_in = "".join(c for c in (payload.phone_last4 or "") if c.isdigit())
    if len(last4_in) != 4:
        _log_attempt(db, surgery_id, success=False, request=request)
        raise HTTPException(status_code=422, detail="Phone last 4 must be 4 digits")

    # Compute on-file last4
    on_file = s.cell_phone or s.phone or ""
    on_file_digits = "".join(c for c in on_file if c.isdigit())
    on_file_last4 = on_file_digits[-4:] if len(on_file_digits) >= 4 else ""

    dob_match = (s.dob == dob_in)
    last4_match = (last4_in == on_file_last4) and bool(on_file_last4)
    if not (dob_match and last4_match):
        _log_attempt(db, surgery_id, success=False, request=request)
        # Generic message — don't leak which field was wrong
        raise HTTPException(status_code=401,
                            detail="Date of birth or phone number doesn't match what we have on file. "
                                   "Please double-check, or call our office at 240-252-2140.")

    _log_attempt(db, surgery_id, success=True, request=request)
    # Auto-unresponsive sweep tracking (audit #13): a successful auth
    # is the lightest engagement signal and resets the 30-day clock.
    s.last_patient_activity_at = now_utc_naive()
    db.commit()
    token = _issue_patient_token(
        surgery_id,
        ptv=int(getattr(s, "portal_token_version", 0) or 0),
    )
    return {
        "token": token,
        "expires_in_seconds": PATIENT_TOKEN_TTL_HOURS * 3600,
    }


def _log_attempt(db: Session, surgery_id: str, *, success: bool, request: Request) -> None:
    ip = request.client.host if request.client else None
    db.add(PatientAuthAttempt(surgery_id=surgery_id, success=success, ip_address=ip))
    db.commit()


# ─── Status ─────────────────────────────────────────────────────────

@router.get("/{surgery_id}/status")
def patient_status(surgery_id: str, db: Session = Depends(get_db),
                    _token: str = Depends(require_patient_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404)

    pat_resp = float(s.patient_responsibility or 0)
    paid = float(s.amount_paid or 0)
    balance = max(0.0, pat_resp - paid)
    balance_clear = balance <= 0 or s.balance_override

    can_pick = (
        balance_clear
        and not s.scheduled_date
        and s.status not in ("cancelled", "completed", "unresponsive")
    )

    procs = s.procedures or []
    proc_names = [p.get("description", "") for p in procs if p.get("description")]

    fee_days_before = cfg(db, "cancellation_fee_days_before")
    fee_amount = cfg(db, "cancellation_fee_amount")
    fee_applies = bool(s.scheduled_date) and 0 <= (s.scheduled_date - date.today()).days <= fee_days_before
    can_cancel = s.status not in ("cancelled", "completed", "unresponsive")

    return {
        "patient_first_name": s.first_name or (s.patient_name or "").split(",")[-1].strip().split(" ")[0],
        "procedure_descriptions": proc_names,
        "is_robotic": bool(s.is_robotic),
        "eligible_facilities": s.eligible_facilities or [],
        "selected_facility": s.selected_facility,
        "scheduled_date": str(s.scheduled_date) if s.scheduled_date else None,
        "scheduled_start_time": (str(s.scheduled_start_time)[:5]
                                  if s.scheduled_start_time else None),
        "patient_responsibility": pat_resp,
        "amount_paid": paid,
        "balance_due": balance,
        "balance_clear": balance_clear,
        "balance_override": bool(s.balance_override),
        "can_pick_date": can_pick,
        "status": s.status,
        "clearance_required": bool(s.clearance_required),
        "sms_consent":   bool(s.sms_consent),
        "cell_phone":    s.cell_phone,
        "cancellation_fee_amount":      fee_amount,
        "cancellation_fee_days_before": fee_days_before,
        "cancellation_fee_applies":     fee_applies,
        "can_cancel":                   can_cancel,
    }


# ─── Available slots ────────────────────────────────────────────────

@router.get("/{surgery_id}/slots")
def patient_slots(surgery_id: str, days_ahead: Optional[int] = None,
                   db: Session = Depends(get_db),
                   _token: str = Depends(require_patient_token)):
    """Return upcoming block days that can fit this surgery's procedure
    classification, grouped by facility. The window end defaults to the
    configurable `patient_booking_window_days` setting (180 days)."""
    from app.services.surgery.settings import cfg
    if days_ahead is None:
        try:
            days_ahead = int(cfg(db, "patient_booking_window_days") or 180)
        except (ValueError, TypeError):
            days_ahead = 180
    s = (db.query(Surgery)
           .filter(Surgery.id == surgery_id)
           .first())
    if not s:
        raise HTTPException(status_code=404)
    if not s.procedure_classification:
        raise HTTPException(status_code=409,
                            detail="Surgery is missing a procedure classification — "
                                   "please call our office.")

    proc_kind = s.procedure_classification
    duration = DURATIONS.get(proc_kind, s.estimated_minutes or 60)
    eligibles = s.eligible_facilities or []
    if not eligibles:
        return {"days": []}

    today = date.today()
    end = today + timedelta(days=days_ahead)

    # Patients can't self-book within 5 business days. Scheduler bypasses this.
    from app.services.surgery.date_picker import patient_min_pickable_date
    min_date = patient_min_pickable_date(db, today=today)

    # Pull all upcoming block days for eligible facilities (with slots eager-loaded)
    block_days = (db.query(BlockDay)
                    .options(joinedload(BlockDay.slots))
                    .filter(BlockDay.facility.in_(eligibles),
                            BlockDay.block_date >= min_date,
                            BlockDay.block_date <= end)
                    .order_by(BlockDay.block_date).all())

    out = []
    for bd in block_days:
        ok, _ = can_fit(db, bd, proc_kind)
        if not ok:
            continue
        # Determine the next available start time within this block.
        # Use the shared helper so office lunch-break logic stays consistent.
        from app.services.surgery.date_picker import _proposed_start_minutes
        existing = sorted((sl for sl in (bd.slots or [])), key=lambda x: x.start_time)
        cursor = _proposed_start_minutes(bd, needed_minutes=duration, db=db)
        block_end_min = bd.end_time.hour * 60 + bd.end_time.minute
        if cursor is None or cursor + duration > block_end_min:
            continue
        proposed_h, proposed_m = divmod(cursor, 60)

        out.append({
            "block_day_id": str(bd.id),
            "facility": bd.facility,
            "block_date": str(bd.block_date),
            "weekday": bd.block_date.strftime("%A"),
            "proposed_start_time": f"{proposed_h:02d}:{proposed_m:02d}",
            "duration_minutes": duration,
            "block_window": f"{bd.start_time.strftime('%H:%M')}–{bd.end_time.strftime('%H:%M')}",
            "cases_already_booked": len(existing),
        })

    return {
        "days": out,
        "procedure_kind": proc_kind,
        "duration_minutes": duration,
    }


# ─── Pick a slot ────────────────────────────────────────────────────

class CardiologistUpdate(BaseModel):
    cardiologist_name: Optional[str] = None
    cardiologist_phone: Optional[str] = None
    cardiologist_fax: Optional[str] = None
    has_cardiologist: bool = True


@router.post("/{surgery_id}/cardiologist")
def patient_update_cardiologist(surgery_id: str, payload: CardiologistUpdate,
                                  db: Session = Depends(get_db),
                                  _token: str = Depends(require_patient_token)):
    """Patient self-reports their cardiologist contact info when clearance
    is required. Stamps clearance_status='request_sent' so the office
    knows we have the destination to send the clearance request to."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404)
    _reject_if_terminal(s)
    if not s.clearance_required:
        raise HTTPException(status_code=409,
                            detail="Clearance isn't required for this surgery.")

    if payload.has_cardiologist:
        if not (payload.cardiologist_name or "").strip():
            raise HTTPException(status_code=422,
                                detail="Cardiologist name is required when 'has cardiologist' is yes.")
        s.cardiologist_name = payload.cardiologist_name.strip()
        s.cardiologist_phone = (payload.cardiologist_phone or "").strip() or None
        s.cardiologist_fax = (payload.cardiologist_fax or "").strip() or None
        s.clearance_status = "request_sent"
    else:
        # Patient said "I don't have a cardiologist" — clearance goes through PCP
        s.cardiologist_name = None
        s.cardiologist_phone = None
        s.cardiologist_fax = None
        s.clearance_status = "required"

    db.commit()
    return {
        "ok": True,
        "clearance_status": s.clearance_status,
        "message": ("Thanks — we'll fax your cardiologist the clearance request."
                     if payload.has_cardiologist
                     else "Thanks — please call your primary care doctor for clearance as soon as possible."),
    }


class PickPayload(BaseModel):
    block_day_id: str


@router.post("/{surgery_id}/pick")
def patient_pick(surgery_id: str, payload: PickPayload,
                  db: Session = Depends(get_db),
                  _token: str = Depends(require_patient_token)):
    """Initial date pick (refuses if a date is already set — use /reschedule)."""
    from app.services.surgery.date_picker import pick_or_reschedule, DatePickerError
    from app.services.surgery.self_schedule import schedule_gate_for_surgery

    s = (db.query(Surgery)
           .filter(Surgery.id == surgery_id)
           .first())
    if not s:
        raise HTTPException(status_code=404)
    if s.scheduled_date:
        raise HTTPException(
            status_code=409,
            detail=f"This surgery already has a scheduled date ({s.scheduled_date}). "
                   "Use the reschedule option instead.",
        )
    # Same payment/status gate the portal flow runs (Fable portal audit C2).
    allowed, reason = schedule_gate_for_surgery(s)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)

    try:
        result = pick_or_reschedule(db, s,
                                      block_day_id=payload.block_day_id,
                                      picked_by="patient:self-service",
                                      enforce_patient_min=True)
    except DatePickerError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except StaleDataError:
        # Surgery row was updated by staff (or another patient action)
        # mid-flight — Surgery.version_id changed under us. Surface a
        # clean 409 instead of a 500. SQLAlchemy already rolled back.
        raise HTTPException(status_code=409,
            detail="This surgery was updated while you were picking a date "
                   "— please refresh and try again")
    # Patient activity (audit #13): a date pick is the strongest
    # possible engagement signal — reset the auto-unresponsive clock.
    s.last_patient_activity_at = now_utc_naive()
    db.commit()

    try:
        from app.services.google_calendar_sync import upsert_event_for_surgery
        upsert_event_for_surgery(db, s)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("calendar sync failed: %s", e)
    try:
        pick_slot = (db.query(SurgerySlot)
                       .filter(SurgerySlot.surgery_id == s.id)
                       .order_by(SurgerySlot.start_time)
                       .first())
        _send_surgery_confirmation_email(db, s, pick_slot, sent_by="patient:self-service")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("confirmation email failed: %s", e)
    try:
        from app.services.surgery.scheduler_notify import notify_scheduler
        notify_scheduler(db, event_kind="date_picked", surgery=s,
                          event_id=f"{s.id}:{now_utc_naive().isoformat()}")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("scheduler notify (pick) failed: %s", e)
    from app.services.surgery.activity import record_activity
    _when = (s.scheduled_date.strftime("%m/%d/%Y") if s.scheduled_date else "")
    record_activity(db, s, "date_picked",
                    f"Patient picked a date: {_when} at {s.selected_facility}")
    db.commit()

    # Surgery is now scheduled — create any linked LARC device requests.
    # Soft-fail: a bridge error must never break the patient's confirmation.
    try:
        from app.services.surgery.device_requests import sync_surgery_device_requests
        sync_surgery_device_requests(db, s, actor_email="system:patient-portal")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("device-request sync failed (pick): %s", e)

    return {
        "ok": True,
        **result,
        "message": "Surgery date confirmed. Watch for a Klara message with consent forms next.",
    }


@router.post("/{surgery_id}/reschedule")
def patient_reschedule(surgery_id: str, payload: PickPayload,
                         db: Session = Depends(get_db),
                         _token: str = Depends(require_patient_token)):
    """Patient self-service reschedule: releases the existing slot and
    claims the new block_day_id. No reschedule-count limit — the system
    just tracks how many times each patient has rescheduled so staff can
    intervene if it gets out of hand."""
    from app.services.surgery.date_picker import pick_or_reschedule, DatePickerError
    from app.services.surgery.self_schedule import schedule_gate_for_surgery

    s = (db.query(Surgery)
           .filter(Surgery.id == surgery_id)
           .first())
    if not s:
        raise HTTPException(status_code=404)
    if not s.scheduled_date:
        raise HTTPException(status_code=409,
                            detail="No date is set yet — use Pick a date instead of Reschedule.")
    # Same payment/status gate as patient_pick / portal_claim_slot.
    # (Fable portal audit C2.)
    allowed, reason = schedule_gate_for_surgery(s)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)

    # 14-day rule for patient reschedules: must call office instead.
    days_to_surgery = (s.scheduled_date - date.today()).days
    if days_to_surgery < 14:
        raise HTTPException(
            status_code=409,
            detail=(f"Your surgery is in {days_to_surgery} day(s). Reschedules within "
                    "14 days must be handled by our office — please call us."),
        )

    prev_date_str = s.scheduled_date.strftime("%m/%d/%Y") if s.scheduled_date else None
    try:
        result = pick_or_reschedule(db, s,
                                      block_day_id=payload.block_day_id,
                                      picked_by="patient:self-service",
                                      enforce_patient_min=True)
    except DatePickerError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except StaleDataError:
        # Surgery row was updated by staff (or another patient action)
        # mid-flight — Surgery.version_id changed under us. Surface a
        # clean 409 instead of a 500. SQLAlchemy already rolled back.
        raise HTTPException(status_code=409,
            detail="This surgery was updated while you were picking a date "
                   "— please refresh and try again")
    # Patient activity (audit #13): reschedule resets the
    # auto-unresponsive clock.
    s.last_patient_activity_at = now_utc_naive()
    db.commit()

    try:
        from app.services.google_calendar_sync import upsert_event_for_surgery
        upsert_event_for_surgery(db, s)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("calendar sync failed: %s", e)
    try:
        from app.services.surgery.scheduler_notify import notify_scheduler
        notify_scheduler(db, event_kind="rescheduled", surgery=s,
                          event_id=f"{s.id}:{now_utc_naive().isoformat()}",
                          extra={"prev_date": prev_date_str})
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("scheduler notify (reschedule) failed: %s", e)
    from app.services.surgery.activity import record_activity
    _when = (s.scheduled_date.strftime("%m/%d/%Y") if s.scheduled_date else "")
    record_activity(db, s, "rescheduled", f"Patient rescheduled to {_when}")
    db.commit()

    return {
        "ok": True,
        **result,
        "message": "Surgery rescheduled. Your previous appointment time has been released.",
    }


class SelectSlotIn(BaseModel):
    block_day_id: str
    start_time: str          # "HH:MM"


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


# NOTE: keep in sync with the copy in app/services/surgery/self_schedule.py
def _default_duration_for(db, surgery, block_day) -> int:
    """Resolve allotted duration:
       1. Coordinator's explicit Surgery.duration_minutes wins.
       2. Then a SurgeryProcedureTemplate for this procedure_kind. If the
          surgery is flagged `complex`, prefer a template whose name
          contains "complex".
       3. Else fall back to the kind→minutes map.
    """
    from app.models.surgery_config import SurgeryProcedureTemplate
    if surgery and surgery.duration_minutes:
        return surgery.duration_minutes

    kind = block_day.block_kind
    templates = (db.query(SurgeryProcedureTemplate)
                   .filter(SurgeryProcedureTemplate.procedure_kind == kind,
                            SurgeryProcedureTemplate.is_active.is_(True))
                   .order_by(SurgeryProcedureTemplate.name.asc())
                   .all())
    if templates:
        if surgery and surgery.complexity == "complex":
            for t in templates:
                if "complex" in (t.name or "").lower():
                    return t.default_duration_minutes
        return templates[0].default_duration_minutes

    fallback = {"office": 30, "minor": 60, "major": 120,
                 "robotic_180": 180, "robotic_240": 240}
    return fallback.get(kind, 60)


@router.post("/{surgery_id}/select-slot")
def patient_select_slot(
    surgery_id: str,
    payload: SelectSlotIn,
    db: Session = Depends(get_db),
    _token: str = Depends(require_patient_token),
):
    """Patient self-schedules into a specific block-day slot by start time.
    Magic-link flow. Portal flow uses /api/patient/portal/{sid}/slots/.../claim.

    Applies the same schedule_gate_for_surgery check the portal flow runs
    — without it, a patient with an unpaid balance or a cancelled
    surgery could self-schedule via the magic link (the UI gated, the
    API did not). (Fable portal audit C2.)
    """
    from app.services.surgery.self_schedule import (
        claim_slot_for_patient, SelfScheduleError, schedule_gate_for_surgery,
    )
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    allowed, reason = schedule_gate_for_surgery(s)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)
    try:
        result = claim_slot_for_patient(
            db, s,
            block_day_id=payload.block_day_id,
            start_time_str=payload.start_time,
            sent_by="patient:self-service",
        )
    except SelfScheduleError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"ok": True, **result}


class CancelPayload(BaseModel):
    reason_text: Optional[str] = None   # patient's free-text reason


@router.post("/{surgery_id}/cancel")
def patient_cancel(surgery_id: str, payload: CancelPayload,
                    db: Session = Depends(get_db),
                    _token: str = Depends(require_patient_token)):
    """Patient self-cancels their surgery. Reason is always 'patient'.
    The configured within-N-days window applies the configured fee
    (cancellation_fee_days_before / cancellation_fee_amount)."""
    from app.models.surgery import SurgeryCancellation, SurgerySlot

    s = (db.query(Surgery)
           .filter(Surgery.id == surgery_id)
           .first())
    if not s:
        raise HTTPException(status_code=404)
    # Tighten the previous "cancelled/completed only" guard. unresponsive
    # means staff have already decided this case is closed; the patient
    # shouldn't be able to reopen the surgery via a portal cancel that
    # creates a new SurgeryCancellation row and a calendar churn.
    if s.status in ("cancelled", "completed", "unresponsive"):
        raise HTTPException(status_code=409, detail="This surgery is no longer active.")

    days_before = cfg(db, "cancellation_fee_days_before")
    amount = cfg(db, "cancellation_fee_amount")
    fee_required = bool(s.scheduled_date) and 0 <= (s.scheduled_date - date.today()).days <= days_before
    refund_required = bool(s.amount_paid and float(s.amount_paid) > 0)

    # Free the booked slot
    freed_block_day_id = None
    held_slot = (db.query(SurgerySlot)
                   .filter(SurgerySlot.surgery_id == s.id).first())
    if held_slot:
        freed_block_day_id = str(held_slot.block_day_id)
        db.delete(held_slot)

    s.status = "cancelled"
    s.scheduled_date = None
    s.scheduled_start_time = None

    notes = "Patient self-cancelled via portal."
    if payload.reason_text:
        notes += f"\nPatient reason: {payload.reason_text.strip()}"

    db.add(SurgeryCancellation(
        surgery_id=s.id,
        cancelled_by="patient:self-service",
        reason="patient",
        fee_required=fee_required,
        refund_required=refund_required,
        notes=notes,
    ))
    try:
        db.commit()
    except StaleDataError:
        # Staff updated the surgery in parallel — the StaleDataError
        # means SQLAlchemy's optimistic lock fired and our commit was
        # aborted. Surface a clean 409 so the portal can prompt the
        # patient to retry.
        db.rollback()
        raise HTTPException(status_code=409,
            detail="This surgery was updated while you were cancelling "
                   "— please refresh and try again")
    try:
        from app.services.google_calendar_sync import delete_event_for_surgery
        delete_event_for_surgery(db, s)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("calendar sync failed: %s", e)
    try:
        from app.services.surgery.scheduler_notify import notify_scheduler
        notify_scheduler(db, event_kind="cancelled", surgery=s,
                          event_id=f"{s.id}:{now_utc_naive().isoformat()}",
                          extra={"fee_required": fee_required,
                                 "fee_amount": amount,
                                 "refund_required": refund_required,
                                 "reason": (payload.reason_text or "").strip() or None})
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("scheduler notify (cancel) failed: %s", e)
    from app.services.surgery.activity import record_activity
    _reason = (payload.reason_text or "").strip() or "no reason given"
    record_activity(db, s, "cancelled", f"Patient cancelled ({_reason})")
    db.commit()

    # Void any still-live BoldSign consent envelopes so the patient can't
    # complete a consent for a surgery they just cancelled. Skip terminal
    # statuses (signed/completed/voided/declined/expired). Mirrors the
    # coordinator-cancel path.
    from app.services.boldsign_envelopes import (
        void_envelope_row, BoldSignEnvelopeError,
    )
    TERMINAL = {"signed", "completed", "voided", "declined", "expired"}
    for env in (s.consent_envelopes or []):
        if (env.status or "").lower() in TERMINAL:
            continue
        try:
            void_envelope_row(db, env, reason="Patient self-cancelled")
        except Exception as ve:
            import logging
            logging.getLogger(__name__).warning(
                "BoldSign void on patient cancel failed for %s: %s",
                env.id, ve,
            )

    # HIPAA audit — the central audit_logs row. cancelled_by on the
    # SurgeryCancellation row is canonical, but a CANCEL action here
    # keeps the per-resource audit timeline complete.
    from app.services.audit_service import log_action
    log_action(
        db,
        action="CANCEL",
        resource_type="surgery",
        resource_id=str(s.id),
        patient_id=s.chart_number or None,
        user_id="patient:self-service",
        user_name="patient (self-service)",
        description=(f"Patient self-cancelled (fee_required: {fee_required}, "
                     f"refund_required: {refund_required})"),
    )

    msg = "Your surgery has been cancelled."
    if fee_required:
        msg += (f" Per practice policy, cancellations within {days_before} days of surgery "
                f"incur a ${amount} fee. Our office will contact you with details.")
    if refund_required:
        msg += " Any amount you've already paid will be refunded."

    return {
        "ok": True,
        "status": s.status,
        "fee_required": fee_required,
        "refund_required": refund_required,
        "freed_block_day_id": freed_block_day_id,
        "message": msg,
    }


# ─── SMS consent (patient self-service) ─────────────────────────────

class PatientSmsConsentIn(BaseModel):
    sms_consent: bool
    cell_phone:  Optional[str] = None


@router.post("/{surgery_id}/sms-consent")
def patient_sms_consent(
    surgery_id: str,
    payload: PatientSmsConsentIn,
    db: Session = Depends(get_db),
    _token: str = Depends(require_patient_token),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    _reject_if_terminal(s)
    from datetime import datetime as _dt
    s.sms_consent = bool(payload.sms_consent)
    if s.sms_consent:
        s.sms_consented_at = _dt.utcnow()
        s.sms_consented_by = "patient:self-service"
        if payload.cell_phone and payload.cell_phone.strip():
            s.cell_phone = payload.cell_phone.strip()
    else:
        s.sms_consented_at = None
        s.sms_consented_by = None
    db.commit()
    return {
        "sms_consent": s.sms_consent,
        "cell_phone":  s.cell_phone,
        "sms_consented_at": s.sms_consented_at.isoformat() if s.sms_consented_at else None,
    }


# ─── FMLA paperwork upload (patient self-service) ──────────────────

@router.post("/{surgery_id}/upload-fmla")
async def patient_upload_fmla(
    surgery_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _token: str = Depends(require_patient_token),
):
    """Patient uploads their FMLA paperwork via the magic-link portal.

    Routes through store_upload (same path the regular portal uses)
    instead of os.path.join with the attacker-controlled file.filename
    against a hardcoded developer-laptop path. The old code wrote
    PHI to a path that didn't exist on Cloud Run and was an
    arbitrary-file-write via '../' in file.filename. Saved as
    kind='fmla_completed' so the portal_fmla view sees it and the
    fmla_status='submitted' transition fires. (Fable portal audit C1.)
    """
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404)
    _reject_if_terminal(s)

    contents = await file.read()
    from app.services.surgery.uploads import store_upload, UploadError
    try:
        doc = store_upload(
            db, s, kind="fmla_completed",
            filename=file.filename or "fmla.pdf",
            file_bytes=contents,
            content_type=file.content_type or "application/octet-stream",
            uploaded_by="patient:self-service",
        )
    except UploadError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    # Mirror portal_fmla_upload's transition logic — if the fee was
    # paid, the upload completes the FMLA submission flow.
    if s.fmla_fee_paid and not s.fmla_status:
        s.fmla_status = "submitted"
        db.commit()

    return {
        "ok": True,
        "id": str(doc.id),
        "filename": doc.filename,
        "kind": doc.kind,
        "fmla_status": s.fmla_status,
        "message": "Thanks! Your FMLA paperwork has been received.",
    }
