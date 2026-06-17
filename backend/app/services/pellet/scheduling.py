"""Pellet scheduling: recurrence -> fixed-length slot materialization, the
booking gate, and booking/reschedule/cancel/complete. Modeled on
surgery/block_schedule.py but simpler (per-location, capacity 1).
This task (T2) implements recurrence + materialization only."""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.services.pellet.settings import cfg
from app.models.pellet_schedule import PelletAvailabilityTemplate, PelletSlot


def _dates_for_template(t, start: date, end: date) -> list[date]:
    """Every date in [start, end] this template covers, respecting its
    effective window."""
    lo = max(start, t.effective_from) if t.effective_from else start
    hi = min(end, t.effective_through) if t.effective_through else end
    out: list[date] = []
    if t.recurrence_kind == "specific_dates":
        for s in (t.specific_dates or []):
            try:
                d = date.fromisoformat(s)
            except ValueError:
                continue
            if lo <= d <= hi:
                out.append(d)
        return sorted(out)
    d = lo
    while d <= hi:
        if t.recurrence_kind == "daily":
            out.append(d)
        elif t.recurrence_kind == "weekly" and d.weekday() == t.weekday:
            out.append(d)
        elif t.recurrence_kind == "weekly_nth" and d.weekday() == t.weekday:
            nth = (d.day - 1) // 7 + 1
            if nth in (t.nth_in_month or []):
                out.append(d)
        elif t.recurrence_kind == "monthly_day":
            last = calendar.monthrange(d.year, d.month)[1]
            if d.day == min(t.day_of_month or 1, last):
                out.append(d)
        d += timedelta(days=1)
    return out


def _slot_times(start: time, end: time, minutes: int) -> list[tuple[time, time]]:
    out = []
    cur = datetime.combine(date(2000, 1, 1), start)
    end_dt = datetime.combine(date(2000, 1, 1), end)
    step = timedelta(minutes=minutes)
    while cur + step <= end_dt:
        out.append((cur.time(), (cur + step).time()))
        cur += step
    return out


def materialize_pellet_slots(db: Session, *, days_ahead: int | None = None,
                             today: date | None = None) -> dict:
    """Walk active templates; create open PelletSlot rows for the horizon.
    Idempotent (unique on location+date+start_time); never touches existing
    slots (booked/blocked/addon included)."""
    horizon = days_ahead if days_ahead is not None else int(cfg(db, "schedule_horizon_days"))
    default_min = int(cfg(db, "slot_minutes"))
    start = today or datetime.utcnow().date()
    end = start + timedelta(days=horizon)
    created = 0
    existing = {(s.location, s.slot_date, s.start_time)
                for s in db.query(PelletSlot.location, PelletSlot.slot_date,
                                  PelletSlot.start_time).all()}
    for t in (db.query(PelletAvailabilityTemplate)
                .filter(PelletAvailabilityTemplate.active.is_(True)).all()):
        mins = t.slot_minutes or default_min
        for d in _dates_for_template(t, start, end):
            for (st, et) in _slot_times(t.start_time, t.end_time, mins):
                key = (t.location, d, st)
                if key in existing:
                    continue
                db.add(PelletSlot(template_id=t.id, location=t.location,
                                  provider=t.provider, slot_date=d,
                                  start_time=st, end_time=et, status="open"))
                existing.add(key)
                created += 1
    db.flush()
    return {"created": created, "horizon_days": horizon}
