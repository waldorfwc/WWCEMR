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
from decimal import Decimal
from typing import Optional

import os

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models.surgery import (
    BlockDay, PatientAuthAttempt, Surgery, SurgeryFile, SurgeryMilestone, SurgerySlot,
    SurgeryNote,
)
from app.services.surgery_block_schedule import (
    DURATIONS, can_fit, book_slot, CapacityViolation,
)
from app.services.surgery_slot_conflict import overlapping_slot
from app.services.surgery_blackout_conflict import is_date_blacked_out

log = logging.getLogger(__name__)

router = APIRouter(prefix="/p/surgery", tags=["patient-surgery"])


# ─── Token helpers ──────────────────────────────────────────────────

PATIENT_TOKEN_TTL_HOURS = 1
LOCKOUT_FAILS = 3
LOCKOUT_WINDOW_MIN = 15
PATIENT_TOKEN_AUDIENCE = "wwc:patient-surgery"


def _issue_patient_token(surgery_id: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=PATIENT_TOKEN_TTL_HOURS)
    return jwt.encode(
        {"sub": str(surgery_id), "aud": PATIENT_TOKEN_AUDIENCE, "exp": expire},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def _verify_patient_token(token: str, surgery_id: str) -> bool:
    try:
        payload = jwt.decode(token, settings.secret_key,
                             algorithms=[settings.algorithm],
                             audience=PATIENT_TOKEN_AUDIENCE)
    except JWTError:
        return False
    return payload.get("sub") == str(surgery_id)


def require_patient_token(surgery_id: str,
                            authorization: Optional[str] = Header(None)) -> str:
    """Raises 401 if the token doesn't match this surgery."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing patient token")
    token = authorization[7:]
    if not _verify_patient_token(token, surgery_id):
        raise HTTPException(status_code=401, detail="Invalid or expired patient token")
    return token


# ─── Lockout check ──────────────────────────────────────────────────

def _is_locked_out(db: Session, surgery_id: str) -> bool:
    cutoff = datetime.utcnow() - timedelta(minutes=LOCKOUT_WINDOW_MIN)
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
    token = _issue_patient_token(surgery_id)
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
    }


# ─── Available slots ────────────────────────────────────────────────

@router.get("/{surgery_id}/slots")
def patient_slots(surgery_id: str, days_ahead: int = 180,
                   db: Session = Depends(get_db),
                   _token: str = Depends(require_patient_token)):
    """Return upcoming block days that can fit this surgery's procedure
    classification, grouped by facility. Default window: 6 months out."""
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
    from app.services.surgery_date_picker import patient_min_pickable_date
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
        from app.services.surgery_date_picker import _proposed_start_minutes
        existing = sorted((sl for sl in (bd.slots or [])), key=lambda x: x.start_time)
        cursor = _proposed_start_minutes(bd)
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
    from app.services.surgery_date_picker import pick_or_reschedule, DatePickerError

    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
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

    try:
        result = pick_or_reschedule(db, s,
                                      block_day_id=payload.block_day_id,
                                      picked_by="patient:self-service",
                                      enforce_patient_min=True)
    except DatePickerError as e:
        raise HTTPException(status_code=409, detail=str(e))

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
    from app.services.surgery_date_picker import pick_or_reschedule, DatePickerError

    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id)
           .first())
    if not s:
        raise HTTPException(status_code=404)
    if not s.scheduled_date:
        raise HTTPException(status_code=409,
                            detail="No date is set yet — use Pick a date instead of Reschedule.")

    # 14-day rule for patient reschedules: must call office instead.
    days_to_surgery = (s.scheduled_date - date.today()).days
    if days_to_surgery < 14:
        raise HTTPException(
            status_code=409,
            detail=(f"Your surgery is in {days_to_surgery} day(s). Reschedules within "
                    "14 days must be handled by our office — please call us."),
        )

    try:
        result = pick_or_reschedule(db, s,
                                      block_day_id=payload.block_day_id,
                                      picked_by="patient:self-service",
                                      enforce_patient_min=True)
    except DatePickerError as e:
        raise HTTPException(status_code=409, detail=str(e))

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


def _default_duration_for(db: Session, surgery: Surgery, block_day: BlockDay) -> int:
    """Look up procedure-template duration; fall back to procedure_kind map."""
    from app.models.surgery_config import SurgeryProcedureTemplate
    kind = block_day.block_kind
    template = (db.query(SurgeryProcedureTemplate)
                  .filter(SurgeryProcedureTemplate.procedure_kind == kind,
                          SurgeryProcedureTemplate.is_active.is_(True))
                  .order_by(SurgeryProcedureTemplate.name.asc())
                  .first())
    if template:
        return template.default_duration_minutes
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
    """Patient self-schedules into a specific block-day slot by start time."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    bd = db.query(BlockDay).filter(BlockDay.id == payload.block_day_id).first()
    if not bd:
        raise HTTPException(status_code=404, detail="block day not found")

    blackout = is_date_blacked_out(db, bd.block_date, bd.facility)
    if blackout:
        raise HTTPException(
            status_code=409,
            detail=f"that date is blocked: {blackout.label or blackout.reason} "
                   f"({blackout.scope})",
        )

    start = _parse_hhmm(payload.start_time)
    duration = _default_duration_for(db, s, bd)

    # Conflict check: reject if the new slot's time window overlaps any existing slot.
    conflict = overlapping_slot(db, bd.id, start, duration)
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"that time overlaps an existing slot at "
                   f"{conflict.start_time.strftime('%H:%M')} "
                   f"({conflict.duration_minutes} min)",
        )
    slot = SurgerySlot(
        block_day_id=bd.id, surgery_id=s.id,
        start_time=start, duration_minutes=duration,
        procedure_kind=bd.block_kind,
    )
    db.add(slot)
    s.scheduled_date = bd.block_date
    s.selected_facility = bd.facility
    db.add(SurgeryNote(
        surgery_id=s.id,
        created_by="patient:self-service",
        content=(f"Patient self-scheduled {bd.block_date} {start.strftime('%H:%M')} "
                 f"({duration} min) at {bd.facility}."),
    ))
    db.commit()
    return {
        "ok": True,
        "slot_id": str(slot.id),
        "block_day_id": str(bd.id),
        "start_time": start.strftime("%H:%M"),
        "duration_minutes": duration,
    }


class CancelPayload(BaseModel):
    reason_text: Optional[str] = None   # patient's free-text reason


@router.post("/{surgery_id}/cancel")
def patient_cancel(surgery_id: str, payload: CancelPayload,
                    db: Session = Depends(get_db),
                    _token: str = Depends(require_patient_token)):
    """Patient self-cancels their surgery. Reason is always 'patient'.
    Within-14-day rule applies the $351 fee per practice policy."""
    from app.models.surgery import SurgeryCancellation, SurgerySlot

    s = (db.query(Surgery)
           .options(joinedload(Surgery.milestones))
           .filter(Surgery.id == surgery_id)
           .first())
    if not s:
        raise HTTPException(status_code=404)
    if s.status in ("cancelled", "completed"):
        raise HTTPException(status_code=409, detail="This surgery is no longer active.")

    fee_required = False
    if s.scheduled_date:
        days_to_surgery = (s.scheduled_date - date.today()).days
        if 0 <= days_to_surgery <= 14:
            fee_required = True
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
    db.commit()

    msg = "Your surgery has been cancelled."
    if fee_required:
        msg += (" Per practice policy, cancellations within 14 days of surgery "
                "incur a $351 fee. Our office will contact you with details.")
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


# ─── FMLA paperwork upload (patient self-service) ──────────────────

@router.post("/{surgery_id}/upload-fmla")
async def patient_upload_fmla(
    surgery_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _token: str = Depends(require_patient_token),
):
    """Patient uploads their FMLA paperwork via the portal. Saves as a
    SurgeryFile of kind='fmla'."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404)

    # Accept PDFs / images only, max 10 MB
    if file.content_type not in ("application/pdf", "image/jpeg", "image/png",
                                  "image/jpg", "image/heic"):
        raise HTTPException(status_code=422,
                            detail="File must be a PDF or image (JPG / PNG / HEIC).")
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413,
                            detail="File is too large (max 10 MB).")

    uploads_dir = "/Users/wwcclaudecode/Documents/wwc-era-project/backend/uploads/surgery_files"
    os.makedirs(uploads_dir, exist_ok=True)
    safe_name = f"{s.chart_number}_fmla_{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{file.filename}"
    save_path = os.path.join(uploads_dir, safe_name)
    with open(save_path, "wb") as f:
        f.write(contents)

    row = SurgeryFile(
        surgery_id=s.id,
        kind="fmla",
        filename=file.filename,
        path=save_path,
        mime_type=file.content_type,
        size_bytes=len(contents),
        uploaded_by="patient:self-service",
        notes="Uploaded by patient via portal",
    )
    db.add(row); db.commit()
    return {
        "ok": True,
        "filename": file.filename,
        "message": "Thanks! Your FMLA paperwork has been received.",
    }
