"""Shared slot-claim logic for patient self-scheduling.

Used by:
  - patient_surgery.py POST /{surgery_id}/select-slot   (magic-link flow)
  - patient_portal.py POST /{sid}/slots/{block_day_id}/claim (portal flow)

The two callers differ only in auth — the booking semantics are identical
and live here so they can't drift.
"""
from __future__ import annotations

import logging
from datetime import date as _date, time as dtime
from typing import Optional

from sqlalchemy.orm import Session

from app.utils.dt import now_utc_naive
from app.models.surgery import Surgery, SurgeryNote, SurgerySlot, BlockDay
from app.services.surgery.blackout_conflict import is_date_blacked_out
from app.services.surgery.slot_conflict import overlapping_slot
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

    # Key duration off the SURGERY's own procedure_classification, NOT
    # block_day.block_kind. block_kind values (robotic_only|minor_only|
    # major_only|mixed|office) live in a different namespace than
    # procedure_kind (minor|major|office|robotic_180|robotic_240), so
    # using block_kind missed every non-office lookup and silently
    # returned the generic 60 — storing a 240-min robotic as a 60-min
    # slot and corrupting capacity accounting. (audit #2)
    kind = (surgery.procedure_classification if surgery else None) or "minor"
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
    # Terminal-status guard — mirror date_picker.pick_or_reschedule and
    # patient_cancel. A cancelled/completed/unresponsive surgery must not
    # be (re)booked through the shared claim path. (audit #24/#30)
    if surgery.status in ("cancelled", "completed", "unresponsive"):
        raise SelfScheduleError(
            "This surgery is not in a state that accepts a date pick.",
            status_code=409,
        )

    # Patient self-scheduling FREEZE + WINDOW — applies to BOTH a first-time
    # booking and a reschedule so a crafted request can't bypass the picker's
    # "online booking opens on" freeze or the booking-window ceiling. The
    # 5-business-day floor is intentionally NOT applied here for first-time
    # bookings (that's enforced on reschedules below + by the picker). Staff
    # coordinator scheduling does not go through this path.
    from app.services.surgery.date_picker import (
        patient_booking_freeze_date, patient_max_pickable_date,
    )
    _bd_window = (db.query(BlockDay)
                    .filter(BlockDay.id == block_day_id).first())
    if _bd_window:
        _freeze = patient_booking_freeze_date(db)
        if _freeze and _bd_window.block_date < _freeze:
            raise SelfScheduleError(
                f"Online booking is not available until "
                f"{_freeze.strftime('%B %d, %Y')}. Please call our office "
                "at 240-252-2140.",
                status_code=409,
            )
        _ceiling = patient_max_pickable_date(db)
        if _bd_window.block_date > _ceiling:
            raise SelfScheduleError(
                f"That date is beyond our online booking window "
                f"(through {_ceiling.strftime('%B %d, %Y')}). Please call our "
                "office at 240-252-2140.",
                status_code=409,
            )

    # Reschedule-window guard (audit #24). When the surgery ALREADY has a
    # scheduled_date, claiming a slot is effectively a RESCHEDULE — and a
    # reschedule must honour the same window floors that /reschedule and
    # /pick enforce, which the bare schedule_gate (balance + consent) does
    # not. A FIRST-TIME booking (no existing scheduled_date) skips this so
    # initial self-scheduling still works.
    if surgery.scheduled_date is not None:
        # 14-day rule: reschedules inside 14 days go through the office.
        days_to_surgery = (surgery.scheduled_date - _date.today()).days
        if days_to_surgery < 14:
            raise SelfScheduleError(
                f"Your surgery is in {days_to_surgery} day(s). Reschedules "
                "within 14 days must be handled by our office — please call "
                "us at 240-252-2140.",
                status_code=409,
            )
        # 5-business-day floor on the NEW reschedule date (reschedules only;
        # first-time bookings skip this, the freeze/window above still applies).
        from app.services.surgery.date_picker import patient_min_pickable_date
        floor = patient_min_pickable_date(db)
        if _bd_window and _bd_window.block_date < floor:
            raise SelfScheduleError(
                "Online scheduling requires at least 5 business days notice. "
                f"The earliest date you can pick is "
                f"{floor.strftime('%A, %B %d, %Y')}. Please call our office "
                "at 240-252-2140 for sooner availability.",
                status_code=409,
            )

    # SELECT FOR UPDATE on the block_day row to serialize concurrent
    # claims targeting the same day. Without this, two parallel POSTs
    # at the same start_time can both pass the overlap check (which
    # reads surgery_slots in this transaction) and both insert a slot —
    # ending up with two cases at the same time on the same block day.
    bd = (db.query(BlockDay)
            .filter(BlockDay.id == block_day_id)
            .with_for_update()
            .first())
    if not bd:
        raise SelfScheduleError("Block day not found", status_code=404)

    start = _parse_hhmm(start_time_str)
    duration = _default_duration_for(db, surgery, bd)

    # Pass slot window so partial-day blackouts only block when actually
    # overlapping. Whole-day blackouts still block any picked slot since
    # is_date_blacked_out short-circuits to True on whole-day.
    from datetime import time as _t
    _end_min = start.hour * 60 + start.minute + duration
    _end_t = _t(_end_min // 60 % 24, _end_min % 60)
    blackout = is_date_blacked_out(
        db, bd.block_date, bd.facility,
        surgery.surgeon_email,
        start_time=start, end_time=_end_t,
    )
    if blackout:
        raise SelfScheduleError(
            f"That date/time is blocked: {blackout.label or blackout.reason} "
            f"({blackout.scope})",
            status_code=409,
        )

    conflict = overlapping_slot(db, bd.id, start, duration)
    if conflict:
        raise SelfScheduleError(
            f"That time overlaps an existing slot at "
            f"{conflict.start_time.strftime('%H:%M')} "
            f"({conflict.duration_minutes} min)",
            status_code=409,
        )

    # Eligible-facility gate — mirror date_picker.pick_or_reschedule so a
    # patient can't book a wrong-facility slot (e.g. robotic onto an
    # office day). (audit #1)
    proc_kind = surgery.procedure_classification or "minor"
    if bd.facility not in (surgery.eligible_facilities or []):
        raise SelfScheduleError(
            "This facility isn't available for your procedure.",
            status_code=409,
        )

    # Capacity gate — claim_slot_for_patient previously inserted the slot
    # directly, never calling can_fit, so a patient could overbook a
    # block past capacity. Run can_fit under the row lock we already
    # hold, keyed off the surgery's real procedure_classification. (audit #1)
    from app.services.surgery.block_schedule import can_fit
    ok, reason = can_fit(db, bd, proc_kind)
    if not ok:
        raise SelfScheduleError(reason, status_code=409)

    # Release any prior slot for this surgery so a rebook doesn't leave
    # orphan rows. The surgery row's scheduled_date is overwritten below;
    # without this the old slot keeps inflating cases_already_booked on
    # the abandoned block day.
    prior = db.query(SurgerySlot).filter(
        SurgerySlot.surgery_id == surgery.id
    ).all()
    for old in prior:
        db.delete(old)

    slot = SurgerySlot(
        block_day_id=bd.id, surgery_id=surgery.id,
        start_time=start, duration_minutes=duration,
        # procedure_kind belongs to the surgery (robotic_180, minor, etc.)
        # — bd.block_kind is the *block's* classification ("robotic_only",
        # "mixed", ...) and writing it here both poisons can_fit and
        # corrupts the SurgerySlot.procedure_kind column for downstream
        # readers.
        procedure_kind=proc_kind,
    )
    db.add(slot)
    surgery.scheduled_date = bd.block_date
    surgery.scheduled_start_time = start
    surgery.selected_facility = bd.facility
    # Promote ANY active non-terminal status to confirmed (audit #25). A
    # `hold` (or any active) surgery that books with $0 balance + signed
    # consent previously stayed `hold` and dropped off /surgery/calendar.
    # Terminal statuses are blocked by the guard above and never reach here.
    if surgery.status not in ("cancelled", "completed", "unresponsive"):
        surgery.status = "confirmed"
    # A successful claim is patient engagement — reset the auto-unresponsive
    # clock so a freshly-booked patient isn't later mis-swept. The /pick
    # path does this; the shared claim path must too. (audit #31)
    surgery.last_patient_activity_at = now_utc_naive()
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

    Rules (checked in order; coordinator override skips all of them):
      1. Outstanding balance must be $0 (paid or no responsibility)
      2. Consent must be signed (or not required)
    """
    if surgery.schedule_gate_override:
        return True, None

    pt_resp = float(surgery.patient_responsibility or 0)
    paid    = float(surgery.amount_paid or 0)
    if pt_resp > 0 and paid < pt_resp:
        outstanding = pt_resp - paid
        return False, (f"Please pay your balance before booking a surgery date. "
                        f"Outstanding balance: ${outstanding:.2f}.")

    consent = (surgery.consent_status or "not_required").lower()
    if consent in ("signed", "not_required"):
        return True, None

    # consent_status only flips to "signed" once the practice has also
    # countersigned — but the patient should not be blocked from scheduling
    # while waiting on the practice. Treat "patient-side fully signed" the
    # same as "signed" for the purposes of the schedule gate.
    envs = list(surgery.consent_envelopes or [])
    active = [e for e in envs if (e.status or "").lower() not in ("voided", "declined")]
    if active and all(getattr(e, "patient_signed_at", None) for e in active):
        return True, None

    return False, ("Please sign your consent forms before booking a "
                    "surgery date.")
