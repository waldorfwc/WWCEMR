"""Pellet scheduling: recurrence -> fixed-length slot materialization, the
booking gate, and booking/reschedule/cancel/complete. Modeled on
surgery/block_schedule.py but simpler (per-location, capacity 1).
This task (T2) implements recurrence + materialization only."""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.services.pellet.settings import cfg
from app.services.pellet import payments as pay
from app.models.pellet import PelletPatient, PelletVisit
from app.models.pellet_schedule import PelletAvailabilityTemplate, PelletSlot
from app.utils.dt import now_utc_naive


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
    start = today or now_utc_naive().date()
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


class SlotUnavailable(Exception): pass
class NotEligible(Exception): pass


def _open_bookings(db: Session, patient) -> int:
    """Slots this patient has booked whose visit isn't completed/cancelled."""
    return (db.query(PelletSlot)
              .join(PelletVisit, PelletVisit.id == PelletSlot.pellet_visit_id)
              .filter(PelletSlot.status == "booked",
                      PelletVisit.patient_id == patient.id,
                      PelletVisit.status.in_(("new", "in_progress")))
              .count())


def can_schedule(db: Session, patient) -> tuple[bool, str]:
    """All gates: requirements verified + valid consent + payment standing
    (available_insertions > current open bookings)."""
    from app.routers.patient_pellet import _requirements
    for r in _requirements(db, patient):
        if r["status"] != "done":
            return False, f"{r['label']} not complete"
    if pay.available_insertions(db, patient) <= _open_bookings(db, patient):
        return False, "No insertion credit available — purchase to schedule"
    return True, ""


def book_slot(db: Session, *, slot_id: str, patient, by: str) -> PelletVisit:
    # Lock the PATIENT row first so two concurrent bookings for the same
    # patient on different slots can't both pass the credit gate (each would
    # otherwise see open_bookings=0). Serializes the gate per patient.
    db.query(PelletPatient).filter(PelletPatient.id == patient.id).with_for_update().first()
    slot = (db.query(PelletSlot).filter(PelletSlot.id == slot_id)
              .with_for_update().first())
    if slot is None or slot.status != "open":
        raise SlotUnavailable("slot is not available")
    ok, reason = can_schedule(db, patient)
    if not ok:
        raise NotEligible(reason)
    visit = PelletVisit(patient_id=patient.id, visit_kind="repeat", status="new",
                        scheduled_date=slot.slot_date, location=slot.location,
                        provider=slot.provider, created_by=by)
    db.add(visit); db.flush()
    slot.status = "booked"
    slot.pellet_visit_id = visit.id
    return visit


def cancel_booking(db: Session, *, slot_id: str, by: str) -> None:
    slot = (db.query(PelletSlot).filter(PelletSlot.id == slot_id)
              .with_for_update().first())
    if slot is None or slot.status != "booked":
        raise SlotUnavailable("no active booking on this slot")
    if slot.pellet_visit_id:
        v = db.query(PelletVisit).filter(PelletVisit.id == slot.pellet_visit_id).first()
        if v:
            v.status = "cancelled"
    slot.status = "open"
    slot.pellet_visit_id = None


def reschedule_booking(db: Session, *, from_slot_id: str, to_slot_id: str,
                       patient, by: str) -> PelletVisit:
    new = (db.query(PelletSlot).filter(PelletSlot.id == to_slot_id)
             .with_for_update().first())
    if new is None or new.status != "open":
        raise SlotUnavailable("target slot is not available")
    old = (db.query(PelletSlot).filter(PelletSlot.id == from_slot_id)
             .with_for_update().first())
    if old is None or old.status != "booked":
        raise SlotUnavailable("no active booking to move")
    visit = db.query(PelletVisit).filter(PelletVisit.id == old.pellet_visit_id).first()
    old.status = "open"; old.pellet_visit_id = None
    new.status = "booked"; new.pellet_visit_id = visit.id if visit else None
    if visit:
        visit.scheduled_date = new.slot_date
        visit.location = new.location
        visit.provider = new.provider
    return visit


def complete_booking(db: Session, *, slot_id: str, by: str) -> PelletVisit:
    """Staff marks the booked insertion done -> draw down a credit."""
    slot = (db.query(PelletSlot).filter(PelletSlot.id == slot_id)
              .with_for_update().first())
    if slot is None or slot.status != "booked" or not slot.pellet_visit_id:
        raise SlotUnavailable("no active booking on this slot")
    visit = db.query(PelletVisit).filter(PelletVisit.id == slot.pellet_visit_id).first()
    patient = (db.query(PelletPatient).filter(PelletPatient.id == visit.patient_id).first()
               if visit else None)
    if visit is None or patient is None:
        raise SlotUnavailable("booking's visit/patient record is missing")
    pay.consume_insertion(db, patient, by=by, reason="pellet insertion completed")
    visit.status = "inserted"
    visit.inserted_at = now_utc_naive()
    visit.inserted_by = by
    return visit
