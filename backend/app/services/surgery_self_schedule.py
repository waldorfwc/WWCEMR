"""Shared slot-claim logic for patient self-scheduling.

Used by:
  - patient_surgery.py POST /{surgery_id}/select-slot   (magic-link flow)
  - patient_portal.py POST /{sid}/slots/{block_day_id}/claim (portal flow)

The two callers differ only in auth — the booking semantics are identical
and live here so they can't drift.
"""
from __future__ import annotations

import logging
from datetime import time as dtime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgeryNote, SurgerySlot, BlockDay
from app.services.surgery_blackout_conflict import is_date_blacked_out
from app.services.surgery_slot_conflict import overlapping_slot
from app.services.google_calendar_sync import upsert_event_for_surgery
# Import the existing confirmation helper from patient_surgery — we don't
# move it because it's still used by other endpoints there.
from app.routers.patient_surgery import _send_surgery_confirmation_email

log = logging.getLogger(__name__)


class SelfScheduleError(Exception):
    """Raised when a slot claim can't proceed. Carries a patient-facing
    message via str() AND an HTTP status_code attribute.

    NOTE: Unlike most other custom exceptions in this codebase (which let
    the router decide the HTTP status), SelfScheduleError carries its own
    status_code so the 404 (block day not found) vs. 409 (blackout,
    overlap) distinction survives across multiple callers. Every caller
    must read `e.status_code`:

        try:
            result = claim_slot_for_patient(...)
        except SelfScheduleError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))

    Hardcoding 409 silently drops the 404 signal.
    """
    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


# NOTE: keep in sync with the copy in app/routers/patient_surgery.py
def _default_duration_for(db: Session, surgery: Surgery, block_day: BlockDay) -> int:
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


def claim_slot_for_patient(
    db: Session,
    surgery: Surgery,
    *,
    block_day_id: str,
    start_time_str: str,
    sent_by: str,
) -> dict:
    """Book the slot. Raises SelfScheduleError if blocked.

    Returns: {slot_id, block_day_id, start_time, duration_minutes}
    """
    bd = db.query(BlockDay).filter(BlockDay.id == block_day_id).first()
    if not bd:
        raise SelfScheduleError("Block day not found", status_code=404)

    blackout = is_date_blacked_out(db, bd.block_date, bd.facility,
                                      surgery.surgeon_email)
    if blackout:
        raise SelfScheduleError(
            f"That date is blocked: {blackout.label or blackout.reason} "
            f"({blackout.scope})",
            status_code=409,
        )

    start = _parse_hhmm(start_time_str)
    duration = _default_duration_for(db, surgery, bd)

    conflict = overlapping_slot(db, bd.id, start, duration)
    if conflict:
        raise SelfScheduleError(
            f"That time overlaps an existing slot at "
            f"{conflict.start_time.strftime('%H:%M')} "
            f"({conflict.duration_minutes} min)",
            status_code=409,
        )

    slot = SurgerySlot(
        block_day_id=bd.id, surgery_id=surgery.id,
        start_time=start, duration_minutes=duration,
        procedure_kind=bd.block_kind,
    )
    db.add(slot)
    surgery.scheduled_date = bd.block_date
    surgery.scheduled_start_time = start
    surgery.selected_facility = bd.facility
    if surgery.status in ("new", "in_progress"):
        surgery.status = "confirmed"
    db.add(SurgeryNote(
        surgery_id=surgery.id,
        created_by=sent_by,
        content=(f"Patient self-scheduled {bd.block_date} "
                 f"{start.strftime('%H:%M')} ({duration} min) at "
                 f"{bd.facility}."),
    ))
    db.commit()
    db.refresh(slot)

    try:
        upsert_event_for_surgery(db, surgery)
    except Exception as e:
        log.warning("calendar sync failed: %s", e)
    try:
        _send_surgery_confirmation_email(db, surgery, slot, sent_by=sent_by)
    except Exception as e:
        log.warning("confirmation email failed: %s", e)
    try:
        # Soft-fail: a BoldSign outage doesn't block the booking. Patient
        # can retry from portal Consent page via POST /consent/resend.
        from app.services.boldsign_envelopes import send_consent_envelopes
        send_consent_envelopes(db, surgery, sent_by=sent_by)
    except Exception as e:
        log.warning("consent envelope send failed: %s", e)

    return {
        "slot_id": str(slot.id),
        "block_day_id": str(bd.id),
        "start_time": start.strftime("%H:%M"),
        "duration_minutes": duration,
    }


def schedule_gate_for_surgery(surgery: Surgery) -> tuple[bool, Optional[str]]:
    """Decide whether a patient may self-schedule.

    Returns (allowed, reason). 'reason' is a patient-facing string when
    not allowed; None when allowed.

    Rules:
      pt_resp <= 0                        → allowed (no balance to pay)
      Surgery.amount_paid >= pt_resp      → allowed (paid in full)
      surgery.schedule_gate_override      → allowed (coordinator override)
      otherwise                           → not allowed, show outstanding amount
    """
    pt_resp = float(surgery.patient_responsibility or 0)
    if pt_resp <= 0:
        return True, None
    paid = float(surgery.amount_paid or 0)
    if paid >= pt_resp:
        return True, None
    if surgery.schedule_gate_override:
        return True, None
    outstanding = pt_resp - paid
    return False, (f"Please make your payment before booking a surgery date. "
                    f"Outstanding balance: ${outstanding:.2f}")
