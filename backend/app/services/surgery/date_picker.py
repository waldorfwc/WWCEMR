"""Surgery date-picker shared service.

Centralises the logic used by both the patient-facing date picker and
the scheduler-facing date picker so both flows enforce identical rules:

  - Balance gate (patient must be paid up unless balance_override is set)
  - Block-day capacity recheck (atomic with slot creation)
  - Slot release on reschedule (old slot deleted before new one claimed)
  - reschedule_count + last_rescheduled_at audit fields
  - patient_picks_date milestone advance

Anything that touches scheduled_date / scheduled_start_time / SurgerySlot
should go through `pick_or_reschedule()` so the audit trail and capacity
rules stay consistent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime, time as _time, timedelta
from app.utils.dt import now_utc_naive
from typing import Optional

from sqlalchemy.orm import Session

from app.models.surgery import BlockDay, Surgery, SurgeryBlackoutDay, SurgerySlot
from app.services.surgery.blackout_conflict import (
    blackouts_for, _scope_matches_blackout,
)
from app.services.surgery.block_schedule import (
    DURATIONS, book_slot, can_fit, CapacityViolation,
)


# Patients can't self-book within this many business days of today. Scheduler
# bypasses this (they call pick_or_reschedule directly without the gate).
PATIENT_MIN_BUSINESS_DAYS_AHEAD = 5


def add_business_days(start: _date, n: int, blackouts: set[_date]) -> _date:
    """Return the date that's `n` business days after `start`. Skips
    weekends and any date in `blackouts` (holidays seeded by the system)."""
    cur = start
    remaining = n
    while remaining > 0:
        cur = cur + timedelta(days=1)
        if cur.weekday() >= 5:           # 5=Sat, 6=Sun
            continue
        if cur in blackouts:
            continue
        remaining -= 1
    return cur


def patient_min_pickable_date(db: Session,
                                today: Optional[_date] = None,
                                n_business_days: int = PATIENT_MIN_BUSINESS_DAYS_AHEAD) -> _date:
    """The earliest date a patient may self-book. Scheduler bypasses this."""
    today = today or _date.today()
    blackouts = {b.blackout_date for b in db.query(SurgeryBlackoutDay).all()}
    return add_business_days(today, n_business_days, blackouts)


class DatePickerError(Exception):
    """Caller should map to HTTP 409 for capacity / gating issues, 404
    for missing block day."""


@dataclass
class AvailableSlot:
    block_day_id: str
    facility: str
    block_date: _date
    proposed_start_time: str       # HH:MM
    duration_minutes: int
    block_window: str              # "07:30–15:30"
    cases_already_booked: int


def _balance_gate(s: Surgery) -> Optional[str]:
    """Return an error message if the patient still owes money. None = OK."""
    pat_resp = float(s.patient_responsibility or 0)
    paid = float(s.amount_paid or 0)
    balance = max(0.0, pat_resp - paid)
    if balance <= 0 or s.balance_override:
        return None
    return (f"Your account has a balance of ${balance:.2f}. "
            "Please pay this through ModMed Pay before picking a date, "
            "or call our office for a payment plan.")


def _blackout_busy(blackouts) -> list:
    """Build (start_min, end_min) busy intervals from PARTIAL-day blackouts.

    Whole-day blackouts are handled separately (they drop the day) and are
    skipped here.
    """
    busy = []
    for bo in blackouts:
        if bo.is_whole_day or bo.start_time is None or bo.end_time is None:
            continue
        bs = bo.start_time.hour * 60 + bo.start_time.minute
        be = bo.end_time.hour * 60 + bo.end_time.minute
        busy.append((bs, be))
    return busy


def _proposed_start_minutes(bd: BlockDay,
                              needed_minutes: Optional[int] = None,
                              db: Optional[Session] = None,
                              extra_busy: tuple = ()) -> Optional[int]:
    """Return the start-time-in-minutes for the next available slot in this
    block.

    For office blocks, slot times are a fixed list (with a lunch break
    gap). For hospital blocks, we walk the day's slots and return the
    FIRST gap big enough to fit `needed_minutes` — gap before the first
    booking, between bookings, or after the last booking. Previously
    only the post-last-slot gap was considered, which incorrectly
    refused dates with usable space at the front of the day (e.g.
    Linkins on 06/17: Andrews 10:30 + Bargas Funes 13:30 left a usable
    07:30–10:30 window that the picker silently skipped).

    `needed_minutes` defaults to a conservative 1 minute when not
    supplied so callers that don't know the duration still get the
    legacy behavior of "first cursor position past the existing slots."

    `extra_busy` is a list of (start_min, end_min) intervals to treat as
    occupied IN ADDITION to existing slots — used to walk gaps around
    partial-day blackout windows so the picker proposes a start AFTER a
    morning PTO instead of dropping the whole day.
    """
    # Treat existing slots as (start_min, end_min) busy intervals, then
    # merge in any extra_busy windows (partial-day blackouts).
    busy = [(sl.start_time.hour * 60 + sl.start_time.minute,
             sl.start_time.hour * 60 + sl.start_time.minute + sl.duration_minutes)
            for sl in (bd.slots or [])]

    # Office: pick from the fixed slot list, return the first not already
    # booked AND not overlapping an extra_busy (blackout) window.
    if bd.facility == "office":
        from app.services.surgery.block_schedule import office_slot_times_min
        taken = {sl.start_time.hour * 60 + sl.start_time.minute for sl in (bd.slots or [])}
        need = needed_minutes or 1
        for t in office_slot_times_min(db):
            if t in taken:
                continue
            if any(t < be and t + need > bs for (bs, be) in extra_busy):
                continue
            return t
        return None  # all office slots taken / blacked out

    busy.extend(tuple(iv) for iv in extra_busy)
    busy.sort(key=lambda iv: iv[0])

    block_start_min = bd.start_time.hour * 60 + bd.start_time.minute
    block_end_min   = bd.end_time.hour * 60 + bd.end_time.minute
    need = needed_minutes or 1

    # Walk gaps in clock order: before-first, between, after-last
    cursor = block_start_min
    for bs, be in busy:
        gap = bs - cursor
        if gap >= need:
            return cursor
        cursor = max(cursor, be)
    if block_end_min - cursor >= need:
        return cursor
    return None


def available_slots_for_surgery(db: Session, s: Surgery, *,
                                  days_ahead: int = 180,
                                  min_date: Optional[_date] = None) -> list[AvailableSlot]:
    """List block days within the window that can fit this surgery.

    Caller decides whether to gate on balance/scheduled_date — this fn
    only checks facility eligibility and capacity.

    `min_date` (optional) excludes any block day before that date. The
    patient-facing slot endpoint passes `patient_min_pickable_date(db)`
    to enforce the 5-business-day rule; the scheduler-side endpoint omits
    it.
    """
    if not s.procedure_classification:
        raise DatePickerError(
            "Surgery is missing a procedure classification — "
            "please call our office."
        )
    proc_kind = s.procedure_classification
    duration = DURATIONS.get(proc_kind, s.estimated_minutes or 60)
    eligibles = s.eligible_facilities or []
    if not eligibles:
        return []

    today = _date.today()
    floor_date = max(today, min_date) if min_date else today
    end = today + timedelta(days=days_ahead)
    block_days = (db.query(BlockDay)
                    .filter(BlockDay.facility.in_(eligibles),
                            BlockDay.block_date >= floor_date,
                            BlockDay.block_date <= end)
                    .order_by(BlockDay.block_date).all())

    # Prefetch ALL blackout rows in the window in ONE query, grouped by date.
    # Previously this called blackouts_for() once PER block day — one query
    # each across the (up to 180-day) horizon. The per-block-day scope match
    # below uses the SAME predicate (_scope_matches_blackout) blackouts_for
    # uses, so results are byte-for-byte identical — only the query count
    # drops (N+1 → 1).
    blackout_rows = (db.query(SurgeryBlackoutDay)
                       .filter(SurgeryBlackoutDay.blackout_date >= floor_date,
                               SurgeryBlackoutDay.blackout_date <= end)
                       .all())
    blackouts_by_date: dict = {}
    for b in blackout_rows:
        blackouts_by_date.setdefault(b.blackout_date, []).append(b)

    out: list[AvailableSlot] = []
    for bd in block_days:
        # Skip the patient's currently-held block day from the offer list
        # only when we're computing "candidates for reschedule"; the caller
        # decides whether to filter. We return all matches here.
        ok, _ = can_fit(db, bd, proc_kind)
        if not ok:
            continue
        # Don't offer blacked-out days/windows. Whole-day blackouts (PTO,
        # holidays, facility closures) remove the day entirely. Partial-day
        # blackouts only block their own window: feed them into the gap-walk
        # as occupied intervals so the picker proposes a start AFTER the
        # blocked window (e.g. morning PTO 07:00–12:00 still offers the free
        # 12:00–15:00 afternoon). (Previously one proposed start was computed
        # and the whole day was dropped if it overlapped any partial blackout,
        # wrongly hiding a block day with a free window.)
        bos = [b for b in blackouts_by_date.get(bd.block_date, [])
               if _scope_matches_blackout(b, bd.facility,
                                          surgeon_email=s.surgeon_email)]
        if any(bo.is_whole_day for bo in bos):
            continue
        extra_busy = _blackout_busy(bos)
        cursor = _proposed_start_minutes(bd, needed_minutes=duration, db=db,
                                          extra_busy=extra_busy)
        block_end_min = bd.end_time.hour * 60 + bd.end_time.minute
        if cursor is None or cursor + duration > block_end_min:
            continue
        h, m = divmod(cursor, 60)
        out.append(AvailableSlot(
            block_day_id=str(bd.id),
            facility=bd.facility,
            block_date=bd.block_date,
            proposed_start_time=f"{h:02d}:{m:02d}",
            duration_minutes=duration,
            block_window=f"{bd.start_time.strftime('%H:%M')}–{bd.end_time.strftime('%H:%M')}",
            cases_already_booked=len(bd.slots or []),
        ))
    return out


def pick_or_reschedule(db: Session, s: Surgery, *, block_day_id: str,
                        picked_by: str, enforce_patient_min: bool = False) -> dict:
    """Place this surgery onto the given block day.

    If the surgery already has a slot, that slot is released first (so
    capacity for the new day is computed correctly and the old time
    becomes available for the waitlist). `picked_by` is stamped onto
    last_rescheduled_by when this is a reschedule.

    `enforce_patient_min=True` blocks dates within 5 business days. Used
    by patient self-service endpoints; scheduler endpoints leave it False.

    Returns: { scheduled_date, scheduled_start_time, facility,
               freed_block_day_id, is_reschedule }
    """
    if s.status in ("cancelled", "completed", "unresponsive"):
        raise DatePickerError(
            "This surgery is not in a state that accepts a date pick."
        )

    bal_err = _balance_gate(s)
    if bal_err:
        raise DatePickerError(bal_err)

    bd = db.query(BlockDay).filter(BlockDay.id == block_day_id).first()
    if not bd:
        raise DatePickerError("Block day not found")
    if bd.facility not in (s.eligible_facilities or []):
        raise DatePickerError("That facility isn't an option for this surgery.")
    if bd.block_date < _date.today():
        raise DatePickerError("That date has passed.")

    if enforce_patient_min:
        floor = patient_min_pickable_date(db)
        if bd.block_date < floor:
            raise DatePickerError(
                f"Online scheduling requires at least 5 business days notice. "
                f"The earliest date you can pick is {floor.strftime('%A, %B %d, %Y')}. "
                "Please call our office at 240-252-2140 for sooner availability."
            )

    # Release the existing slot (if any) BEFORE recomputing capacity for the
    # target block. Important because the patient might be picking a date
    # on the same block day they're already on (no-op but still safe).
    existing_slot = (db.query(SurgerySlot)
                       .filter(SurgerySlot.surgery_id == s.id).first())
    freed_block_day_id: Optional[str] = None
    is_reschedule = False
    if existing_slot:
        freed_block_day_id = str(existing_slot.block_day_id)
        db.delete(existing_slot)
        db.flush()
        is_reschedule = True

    proc_kind = s.procedure_classification or "minor"
    duration = DURATIONS.get(proc_kind, s.estimated_minutes or 60)

    # Recompute next-available start on the target block (fresh, after
    # the existing slot was deleted if it was on this same block). Honor
    # blackouts the same way the offer list does so the proposed time is
    # bookable: whole-day blackouts reject the date, partial-day windows
    # push the proposed start past the blocked window.
    db.refresh(bd)
    bos = blackouts_for(db, bd.block_date, bd.facility,
                        surgeon_email=s.surgeon_email)
    if any(bo.is_whole_day for bo in bos):
        raise DatePickerError("That date is no longer available — please pick another.")
    cursor = _proposed_start_minutes(bd, needed_minutes=duration, db=db,
                                      extra_busy=_blackout_busy(bos))
    block_end_min = bd.end_time.hour * 60 + bd.end_time.minute
    if cursor is None or cursor + duration > block_end_min:
        raise DatePickerError("That date no longer has room — please pick another.")
    h, m = divmod(cursor, 60)

    try:
        slot = book_slot(
            db, block_day_id=str(bd.id), surgery_id=str(s.id),
            start_time=_time(h, m),
            duration_minutes=duration,
            procedure_kind=proc_kind,
        )
    except CapacityViolation as exc:
        raise DatePickerError(str(exc))
    except ValueError as exc:
        raise DatePickerError(str(exc))

    # Surgery row state
    s.scheduled_date = bd.block_date
    s.scheduled_start_time = _time(h, m)
    s.selected_facility = bd.facility
    # Date picked → Pre-Surgery (internal value: "confirmed")
    if s.status in ("new", "in_progress"):
        s.status = "confirmed"

    if is_reschedule:
        s.reschedule_count = (s.reschedule_count or 0) + 1
        s.last_rescheduled_at = now_utc_naive()
        s.last_rescheduled_by = picked_by
        # Clear hospital-posting state since the new date needs a fresh
        # boarding slip / fax.
        s.calendar_invite_sent_at = None
        # Re-arm the boarding-slip auto-email for the new date.
        s.boarding_slip_auto_emailed_at = None

    db.commit()
    db.refresh(s)
    return {
        "scheduled_date": str(s.scheduled_date),
        "scheduled_start_time": s.scheduled_start_time.strftime("%H:%M"),
        "facility": s.selected_facility,
        "is_reschedule": is_reschedule,
        "freed_block_day_id": freed_block_day_id,
        "reschedule_count": s.reschedule_count or 0,
    }
