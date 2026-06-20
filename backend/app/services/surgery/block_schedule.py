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


def materialize_block_days(db: Session, *, days_ahead: int | None = None) -> dict:
    """Walk all active BlockSchedules and create BlockDay rows for the
    next `days_ahead` days. Skips office-wide blackout dates entirely
    and facility-scoped blackouts for that facility.

    Idempotent: existing BlockDay rows (matching facility+date) are
    refreshed in place (e.g. if the schedule's hours change).

    When `days_ahead` is not passed, the horizon comes from the
    schedule_horizon_days surgery setting (default 180).
    """
    if days_ahead is None:
        from app.services.surgery.settings import cfg
        days_ahead = cfg(db, "schedule_horizon_days")

    today = date.today()
    end = today + timedelta(days=days_ahead)

    # Build blackout sets so we can filter quickly. Only WHOLE-DAY
    # blackouts kill BlockDay creation — partial-day blackouts leave
    # the BlockDay in place so the booking flow can offer slots outside
    # the blacked-out window (book_slot does an overlap-aware check at
    # booking time). Provider-scope whole-day blackouts also skip
    # (single-surgeon practice — provider unavailable = day unavailable).
    blackouts = db.query(SurgeryBlackoutDay).filter(
        SurgeryBlackoutDay.blackout_date >= today,
        SurgeryBlackoutDay.blackout_date <= end,
    ).all()
    def _is_whole_day(b):
        return b.start_time is None and b.end_time is None
    office_blackouts: set[date] = set()
    provider_blackouts: set[date] = set()
    facility_blackouts: dict[str, set[date]] = {}
    for b in blackouts:
        if not _is_whole_day(b):
            continue
        if b.scope == "office":
            office_blackouts.add(b.blackout_date)
        elif b.scope == "provider":
            provider_blackouts.add(b.blackout_date)
        elif b.scope == "facility" and b.facility:
            facility_blackouts.setdefault(b.facility, set()).add(b.blackout_date)

    schedules = (db.query(BlockSchedule)
                    .filter(BlockSchedule.effective_from <= end)
                    .all())

    # Only consider BlockDays the materializer created itself (is_addon
    # is False). Ad-hoc add-on blocks created via /admin/block-days are
    # owned by the coordinator and must not be touched here, even when
    # they share (facility, block_date) with a schedule-derived one.
    # Multiple windows per day are now allowed, so this dict is keyed
    # on (facility, date, start_time) to avoid collisions between
    # different schedules that happen to land on the same date.
    existing = {(bd.facility, bd.block_date, bd.start_time): bd
                for bd in db.query(BlockDay)
                              .filter(BlockDay.block_date >= today,
                                      BlockDay.block_date <= end,
                                      BlockDay.is_addon.is_(False))
                              .all()}

    created = updated = blocked = 0

    for sched in schedules:
        for d in _dates_for_schedule(sched, today, end):
            # Office-wide or provider-wide holiday → skip everywhere.
            # (Single-surgeon practice: provider unavailable closes the
            # whole day; multi-surgeon migration will need surgeon_email
            # context to narrow this scope.)
            if d in office_blackouts or d in provider_blackouts:
                blocked += 1
                continue
            # Facility-scoped closure → skip just this facility
            if d in facility_blackouts.get(sched.facility, set()):
                blocked += 1
                continue

            key = (sched.facility, d, sched.start_time)
            if key in existing:
                bd = existing[key]
                bd.block_kind = sched.block_kind
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

    # Reconcile: a block day materialized earlier can become stale when a
    # whole-day blackout is added afterward (the typical ad-hoc PTO case —
    # the create loop above only SKIPS new creation, it never removed the
    # already-existing row). Delete those stale, schedule-derived, EMPTY
    # block days so they stop surfacing in the date picker. Block days that
    # already have a booked slot are left alone — those are real conflicts
    # surfaced via find_blocked_conflicts and must not be silently deleted
    # (that would orphan a patient's scheduled date).
    # Prefetch which existing block days already have a slot in ONE query
    # instead of lazy-loading bd.slots per block day (N+1). DISTINCT
    # block_day_id over the relevant ids gives us the "has any slot" set.
    removed = 0
    existing_ids = [bd.id for bd in existing.values()]
    slotted_ids: set = set()
    if existing_ids:
        slotted_ids = {
            row[0] for row in
            db.query(SurgerySlot.block_day_id)
              .filter(SurgerySlot.block_day_id.in_(existing_ids))
              .distinct()
              .all()
        }
    for (fac, bdate, _st), bd in list(existing.items()):
        blacked = (bdate in office_blackouts
                   or bdate in provider_blackouts
                   or bdate in facility_blackouts.get(fac, set()))
        if blacked and bd.id not in slotted_ids:
            db.delete(bd)
            removed += 1

    db.commit()
    return {
        "days_ahead": days_ahead,
        "blockdays_created": created,
        "blockdays_updated": updated,
        "blockdays_removed": removed,
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


# Default capacity rules — mirror of the previously hardcoded logic.
# Overridable via SurgeryConfig key "capacity_rules" (validated shape in
# surgery_config.FacilityCapacity).
DEFAULT_CAPACITY_RULES = {
    "medstar": {
        "kind": "robotic",
        "options": [{"case_kind": "robotic_180", "max": 3},
                     {"case_kind": "robotic_240", "max": 2}],
        "exclusive": True,
        "minor_addon": {"after_count": 2, "blocked_at": 3},
    },
    "crmc": {
        "kind": "mix_exclusive",
        "options": [{"case_kind": "minor", "max": 6},
                     {"case_kind": "major", "max": 2}],
    },
    "office": {
        "kind": "fixed_slots",
        "slot_times": ["07:30", "08:30", "09:30", "10:30", "11:30",
                        "14:30", "15:30"],
    },
}


def capacity_rules(db: Session) -> dict:
    """Merged capacity rules: config override per facility, else defaults."""
    rules = {k: dict(v) for k, v in DEFAULT_CAPACITY_RULES.items()}
    if db is not None:
        try:
            from app.services.surgery.settings import cfg
            override = cfg(db, "capacity_rules") or {}
            for fac, r in override.items():
                rules[fac] = r
        except Exception:
            log.warning("bad capacity_rules config; using defaults", exc_info=True)
    return rules


def office_slot_times_min(db: Session) -> list:
    """Office slot start times as minutes-from-midnight (config-driven)."""
    r = capacity_rules(db).get("office") or {}
    times = r.get("slot_times") or DEFAULT_CAPACITY_RULES["office"]["slot_times"]
    out = []
    for t in times:
        h, m = t.split(":")
        out.append(int(h) * 60 + int(m))
    return sorted(out)


class CapacityViolation(Exception):
    """Raised when a slot can't fit on a block day per the rules."""


def can_fit(db: Session, block_day: BlockDay, procedure_kind: str) -> tuple[bool, str]:
    """Return (ok, reason). reason is empty when ok=True.

    Applies the WWC rules + a hard wall of "total scheduled minutes
    can't exceed the block window's actual length" so a short block day
    (e.g. CRMC W1 Mon 9-2 = 300 min) doesn't accept more than fits.
    """
    existing = list(block_day.slots or [])
    counts = {}
    for sl in existing:
        counts[sl.procedure_kind] = counts.get(sl.procedure_kind, 0) + 1

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

    rule = capacity_rules(db).get(block_day.facility)
    if rule is None:
        return False, f"Unknown facility: {block_day.facility}"

    kind = rule.get("kind")
    options = {o["case_kind"]: o["max"] for o in rule.get("options", [])}

    if kind == "robotic":
        if procedure_kind in options:
            if rule.get("exclusive", True):
                for other in options:
                    if other != procedure_kind and counts.get(other, 0) > 0:
                        return False, (f"Day already has a {DURATIONS.get(other)}-min "
                                        f"robotic; can't add {DURATIONS.get(procedure_kind)}-min.")
            if counts.get(procedure_kind, 0) >= options[procedure_kind]:
                return False, (f"Day already has {options[procedure_kind]} × "
                                f"{DURATIONS.get(procedure_kind)}-min robotics (max).")
            return True, ""
        if procedure_kind == "minor" and rule.get("minor_addon"):
            addon = rule["minor_addon"]
            # A minor add-on rides on a canonical block of the PRIMARY robotic
            # kind (the first option, e.g. robotic_180) with no other robotic
            # kind present — mirrors the original "robotic_180 == 2 and
            # robotic_240 == 0" rule. Mixed-robotic days (only reachable via
            # force-booked imports) do not qualify.
            opt_kinds = list(options)
            primary = opt_kinds[0] if opt_kinds else None
            primary_count = counts.get(primary, 0)
            others = sum(counts.get(k, 0) for k in opt_kinds[1:])
            if primary_count >= addon["blocked_at"]:
                return False, (f"Day full with {primary_count} × "
                                f"{DURATIONS.get(primary)}-min robotics — "
                                f"no minor add-ons.")
            if primary_count == addon["after_count"] and others == 0:
                return True, ""
            return False, (f"Minors at {block_day.facility} require "
                            f"{addon['after_count']} × {DURATIONS.get(primary)}-min "
                            f"robotics already booked; currently "
                            f"{primary_count + others}.")
        return False, f"{block_day.facility} block doesn't accept {procedure_kind} cases."

    if kind == "mix_exclusive":
        if procedure_kind not in options:
            return False, f"{block_day.facility} block doesn't accept {procedure_kind} cases."
        for other in options:
            if other != procedure_kind and counts.get(other, 0) > 0:
                return False, (f"Day already has a {other} case; "
                                f"can't mix {procedure_kind}s.")
        if counts.get(procedure_kind, 0) >= options[procedure_kind]:
            return False, (f"Day already has {options[procedure_kind]} "
                            f"{procedure_kind}s (max).")
        return True, ""

    if kind == "fixed_slots":
        if not procedure_kind.startswith("office"):
            return False, f"Office block doesn't accept {procedure_kind} cases."
        max_slots = len(rule.get("slot_times")
                         or DEFAULT_CAPACITY_RULES["office"]["slot_times"])
        if len(existing) >= max_slots:
            return False, f"Day already has {max_slots} office cases (max)."
        return True, ""

    return False, f"Unknown capacity kind for {block_day.facility}: {kind}"


def book_slot(db: Session, *, block_day_id: str, surgery_id: str,
              start_time: time, duration_minutes: int,
              procedure_kind: str) -> SurgerySlot:
    """Create a SurgerySlot after capacity check. Raises CapacityViolation
    on rule failure.

    Concurrency: the BlockDay row is locked SELECT FOR UPDATE so two
    concurrent bookings on the same day (patient magic-link picker, staff
    /book-slot, waitlist claim, self-schedule) can't both pass can_fit
    and double-book MedStar / CRMC capacity. claim_slot_for_patient
    already locks at its own entry point — re-locking the same row in
    the same transaction is a Postgres no-op, so callers that already
    hold the lock pay no cost.
    """
    block_day = (db.query(BlockDay)
                   .filter(BlockDay.id == block_day_id)
                   .with_for_update()
                   .first())
    if not block_day:
        raise ValueError("block day not found")
    surgery = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not surgery:
        raise ValueError("surgery not found")

    # Release any prior slot for this surgery — a rebook would otherwise
    # leave an orphan that keeps consuming capacity on the abandoned
    # block day and shows up in calendar_day_detail. cancel_surgery only
    # frees .first() so a second-slot rebook would survive even the
    # cancellation pathway. (Fable surgery audit C1.1.)
    prior_slots = (db.query(SurgerySlot)
                     .filter(SurgerySlot.surgery_id == surgery.id).all())
    for old in prior_slots:
        db.delete(old)

    # Blackout check — defense in depth. coordinator_schedule,
    # claim_slot_for_patient, and the waitlist book path all check
    # before calling book_slot, but the bulk-import + silent-schedule
    # admin paths bypass those callers. Re-running the check here under
    # the row lock means no booking path can ever land on a blacked-out
    # date/window. Partial-day blackouts only block when the proposed
    # slot's window overlaps the blackout window.
    from app.services.surgery.blackout_conflict import is_date_blacked_out
    slot_end_min = (start_time.hour * 60 + start_time.minute + duration_minutes)
    slot_end_time = time(slot_end_min // 60 % 24, slot_end_min % 60)
    blackout = is_date_blacked_out(
        db, block_day.block_date,
        block_day.facility,
        surgeon_email=surgery.surgeon_email,
        start_time=start_time, end_time=slot_end_time,
    )
    if blackout:
        raise CapacityViolation(
            f"That date/time is blocked: "
            f"{blackout.label or blackout.reason} ({blackout.scope})"
        )

    ok, reason = can_fit(db, block_day, procedure_kind)
    if not ok:
        raise CapacityViolation(reason)

    # Time-overlap check, evaluated *after* the row lock so T2 sees any
    # slot T1 just committed. can_fit only counts cases by kind — it
    # doesn't catch two bookings at the same start_time.
    from app.services.surgery.slot_conflict import overlapping_slot
    conflict = overlapping_slot(db, block_day.id, start_time, duration_minutes)
    if conflict:
        raise CapacityViolation(
            f"That time conflicts with an existing slot at "
            f"{conflict.start_time.strftime('%H:%M')} — pick another."
        )

    # Block-window guard — staff-side coordinator_schedule used to skip
    # this; a 16:30 start + 240 min would otherwise sail past the end of
    # the OR block. (Fable surgery audit C1.3.)
    block_start_min = block_day.start_time.hour * 60 + block_day.start_time.minute
    block_end_min   = block_day.end_time.hour * 60 + block_day.end_time.minute
    slot_start_min  = start_time.hour * 60 + start_time.minute
    if slot_start_min < block_start_min or (slot_start_min + duration_minutes) > block_end_min:
        raise CapacityViolation(
            f"That slot would run from {start_time.strftime('%H:%M')} for "
            f"{duration_minutes} min — outside the block window "
            f"({block_day.start_time.strftime('%H:%M')}–{block_day.end_time.strftime('%H:%M')})."
        )

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
