"""Block schedule materialization + capacity rules.

Materializer: walks all active BlockSchedule rows and creates concrete
BlockDay rows for the next N days, skipping any office-wide blackout
(US holidays seeded in surgery_holiday_seed) and any facility-scoped
blackout (e.g. "MedStar block cancelled 6/3").

Capacity rules (codified):

  MedStar (robotic_only or mixed):
    - Hours 7:30 – 4:30 (540 min)
    - 3 × 180min robotic per day, OR 2 × 240min robotic per day
    - Minor add-ons: only after 2 robotics; not allowed if 3 robotics

  CRMC (minor_only or major_only or mixed):
    - Hours 8:00 – 4:00
    - 6 minors × 90min per day, OR 2 majors × 180min per day (mutually exclusive)

  Office (Thursdays at White Plains):
    - Office procedures only; per-provider availability dictates
"""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.surgery import (
    BlockSchedule, BlockDay, SurgerySlot, SurgeryBlackoutDay, Surgery,
)

log = logging.getLogger(__name__)


# ─── Recurrence → list of dates within a window ──────────────────

def _dates_for_schedule(sched: BlockSchedule, start: date, end: date) -> list[date]:
    """Yield every date in [start, end] that this BlockSchedule covers."""
    if sched.effective_through and end > sched.effective_through:
        end = sched.effective_through
    if start < sched.effective_from:
        start = sched.effective_from
    if start > end:
        return []

    out = []
    if sched.recurrence_kind == "weekly":
        # Every week on `weekday`
        d = start
        while d.weekday() != sched.weekday:
            d += timedelta(days=1)
            if d > end:
                return out
        while d <= end:
            out.append(d)
            d += timedelta(days=7)

    elif sched.recurrence_kind == "weekly_nth":
        # E.g. 1st & 3rd Monday of every month
        nths = sorted(set(sched.nth_in_month or []))
        wd = sched.weekday
        # Iterate month by month
        d = date(start.year, start.month, 1)
        while d <= end:
            # Find each nth occurrence in this month
            first = date(d.year, d.month, 1)
            offset = (wd - first.weekday()) % 7
            first_match = first + timedelta(days=offset)
            for nth in nths:
                m = first_match + timedelta(days=(nth - 1) * 7)
                if m.month == d.month and start <= m <= end:
                    out.append(m)
            # Advance to next month
            if d.month == 12:
                d = date(d.year + 1, 1, 1)
            else:
                d = date(d.year, d.month + 1, 1)

    elif sched.recurrence_kind == "specific_dates":
        for s in (sched.specific_dates or []):
            try:
                dd = datetime.strptime(s[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if start <= dd <= end:
                out.append(dd)

    return sorted(set(out))


def materialize_block_days(db: Session, *, days_ahead: int = 180) -> dict:
    """Walk all active BlockSchedules and create BlockDay rows for the
    next `days_ahead` days. Skips office-wide blackout dates entirely
    and facility-scoped blackouts for that facility.

    Idempotent: existing BlockDay rows (matching facility+date) are
    refreshed in place (e.g. if the schedule's hours change).
    """
    today = date.today()
    end = today + timedelta(days=days_ahead)

    # Build blackout sets so we can filter quickly
    blackouts = db.query(SurgeryBlackoutDay).filter(
        SurgeryBlackoutDay.blackout_date >= today,
        SurgeryBlackoutDay.blackout_date <= end,
    ).all()
    office_blackouts = {b.blackout_date for b in blackouts if b.scope == "office"}
    facility_blackouts: dict[str, set[date]] = {}
    for b in blackouts:
        if b.scope == "facility" and b.facility:
            facility_blackouts.setdefault(b.facility, set()).add(b.blackout_date)

    schedules = (db.query(BlockSchedule)
                    .filter(BlockSchedule.effective_from <= end)
                    .all())

    existing = {(bd.facility, bd.block_date): bd
                for bd in db.query(BlockDay)
                              .filter(BlockDay.block_date >= today,
                                      BlockDay.block_date <= end)
                              .all()}

    created = updated = blocked = 0

    for sched in schedules:
        for d in _dates_for_schedule(sched, today, end):
            # Office-wide holiday → skip everywhere
            if d in office_blackouts:
                blocked += 1
                continue
            # Facility-scoped closure → skip just this facility
            if d in facility_blackouts.get(sched.facility, set()):
                blocked += 1
                continue

            key = (sched.facility, d)
            if key in existing:
                bd = existing[key]
                bd.block_kind = sched.block_kind
                bd.start_time = sched.start_time
                bd.end_time = sched.end_time
                bd.notes = sched.notes
                updated += 1
            else:
                db.add(BlockDay(
                    facility=sched.facility,
                    block_date=d,
                    block_kind=sched.block_kind,
                    start_time=sched.start_time,
                    end_time=sched.end_time,
                    is_addon=False,
                    notes=sched.notes,
                ))
                created += 1

    db.commit()
    return {
        "days_ahead": days_ahead,
        "blockdays_created": created,
        "blockdays_updated": updated,
        "blackout_skips": blocked,
    }


# ─── Capacity rules ──────────────────────────────────────────────

# Per-procedure-kind durations (minutes). These drive capacity checks
# and the default estimated_minutes on newly-classified surgeries.
DURATIONS = {
    "robotic_180": 180,
    "robotic_240": 240,
    "minor": 90,           # CRMC minor
    "major": 180,          # CRMC major
    "office": 60,          # WWC office Thursdays — 60-min slots
}


# Office Thursday slot times (fixed list per practice rule):
#   7:30 AM, 8:30 AM, 9:30 AM, 10:30 AM, 11:30 AM, 2:30 PM, 3:30 PM
# (12:30 PM – 2:30 PM lunch break is skipped.)
# Used by the date-picker to decide which start times to offer.
OFFICE_SLOT_TIMES_MIN = [
    7 * 60 + 30,   # 7:30
    8 * 60 + 30,   # 8:30
    9 * 60 + 30,   # 9:30
    10 * 60 + 30,  # 10:30
    11 * 60 + 30,  # 11:30
    14 * 60 + 30,  # 2:30 PM
    15 * 60 + 30,  # 3:30 PM
]


class CapacityViolation(Exception):
    """Raised when a slot can't fit on a block day per the rules."""


def can_fit(db: Session, block_day: BlockDay, procedure_kind: str) -> tuple[bool, str]:
    """Return (ok, reason). reason is empty when ok=True.

    Applies the WWC rules + a hard wall of "total scheduled minutes
    can't exceed the block window's actual length" so a short block day
    (e.g. CRMC W1 Mon 9-2 = 300 min) doesn't accept more than fits.
    """
    existing = list(block_day.slots or [])
    counts = {"robotic_180": 0, "robotic_240": 0, "minor": 0, "major": 0, "office": 0}
    for sl in existing:
        if sl.procedure_kind in counts:
            counts[sl.procedure_kind] += 1

    # Time-window check first — no slot can push us past end_time
    block_minutes = (
        (block_day.end_time.hour * 60 + block_day.end_time.minute)
        - (block_day.start_time.hour * 60 + block_day.start_time.minute)
    )
    used_minutes = sum(sl.duration_minutes for sl in existing)
    incoming = DURATIONS.get(procedure_kind, 60)
    if used_minutes + incoming > block_minutes:
        return False, (f"Day only has {block_minutes} minutes ({used_minutes} used); "
                        f"a {incoming}-minute case won't fit.")

    if block_day.facility == "medstar":
        if procedure_kind in ("robotic_180", "robotic_240"):
            # Don't mix 180 and 240 on the same day
            if procedure_kind == "robotic_180" and counts["robotic_240"] > 0:
                return False, "Day already has a 240-min robotic; can't add 180-min."
            if procedure_kind == "robotic_240" and counts["robotic_180"] > 0:
                return False, "Day already has a 180-min robotic; can't add 240-min."
            if procedure_kind == "robotic_180" and counts["robotic_180"] >= 3:
                return False, "Day already has 3 × 180-min robotics (max)."
            if procedure_kind == "robotic_240" and counts["robotic_240"] >= 2:
                return False, "Day already has 2 × 240-min robotics (max)."
            return True, ""
        if procedure_kind == "minor":
            # Allowed only after 2 robotics, and never if 3 robotics already booked
            if counts["robotic_180"] >= 3:
                return False, "Day full with 3 × 180-min robotics — no minor add-ons."
            if counts["robotic_180"] == 2 and counts["robotic_240"] == 0:
                return True, ""
            if counts["robotic_240"] == 2:
                return False, "Day full with 2 × 240-min robotics — no minor add-ons."
            return False, ("Minors at MedStar require 2 robotics already booked; "
                            f"currently {counts['robotic_180'] + counts['robotic_240']}.")
        return False, f"MedStar block doesn't accept {procedure_kind} cases."

    if block_day.facility == "crmc":
        if procedure_kind == "minor":
            if counts["major"] > 0:
                return False, "Day already has a major case; can't mix minors."
            if counts["minor"] >= 6:
                return False, "Day already has 6 minors (max)."
            return True, ""
        if procedure_kind == "major":
            if counts["minor"] > 0:
                return False, "Day already has a minor case; can't mix majors."
            if counts["major"] >= 2:
                return False, "Day already has 2 majors (max)."
            return True, ""
        return False, f"CRMC block doesn't accept {procedure_kind} cases."

    if block_day.facility == "office":
        # Office Thursdays use a fixed 7-slot schedule (7:30, 8:30, 9:30,
        # 10:30, 11:30, 2:30, 3:30 — lunch break 12:30–2:30).
        if procedure_kind != "office":
            return False, f"Office block doesn't accept {procedure_kind} cases."
        booked = len(block_day.slots or [])
        if booked >= len(OFFICE_SLOT_TIMES_MIN):
            return False, "Day already has 7 office cases (max)."
        return True, ""

    return False, f"Unknown facility: {block_day.facility}"


def book_slot(db: Session, *, block_day_id: str, surgery_id: str,
              start_time: time, duration_minutes: int,
              procedure_kind: str) -> SurgerySlot:
    """Create a SurgerySlot after capacity check. Raises CapacityViolation
    on rule failure."""
    block_day = db.query(BlockDay).filter(BlockDay.id == block_day_id).first()
    if not block_day:
        raise ValueError("block day not found")
    surgery = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not surgery:
        raise ValueError("surgery not found")

    ok, reason = can_fit(db, block_day, procedure_kind)
    if not ok:
        raise CapacityViolation(reason)

    slot = SurgerySlot(
        block_day_id=block_day.id,
        surgery_id=surgery.id,
        start_time=start_time,
        duration_minutes=duration_minutes,
        procedure_kind=procedure_kind,
    )
    db.add(slot)
    surgery.scheduled_date = block_day.block_date
    surgery.scheduled_start_time = start_time
    surgery.selected_facility = block_day.facility
    if surgery.status in ("new", "in_progress"):
        surgery.status = "confirmed"
    db.commit(); db.refresh(slot)
    return slot
