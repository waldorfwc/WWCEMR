from datetime import date, time, timedelta
from app.models.pellet_schedule import PelletAvailabilityTemplate, PelletSlot
from app.services.pellet import scheduling as sched


def _weekly_template(db, weekday):
    t = PelletAvailabilityTemplate(location="white_plains", recurrence_kind="weekly",
                                   weekday=weekday, start_time=time(9, 0),
                                   end_time=time(12, 0), slot_minutes=60, active=True)
    db.add(t); db.commit(); db.refresh(t)
    return t


def test_dates_for_weekly():
    t = PelletAvailabilityTemplate(location="x", recurrence_kind="weekly", weekday=2,
                                   start_time=time(9), end_time=time(12))
    ds = sched._dates_for_template(t, date(2026, 7, 1), date(2026, 7, 15))
    assert all(d.weekday() == 2 for d in ds)
    assert date(2026, 7, 1) in ds and date(2026, 7, 8) in ds


def test_dates_for_monthly_day():
    t = PelletAvailabilityTemplate(location="x", recurrence_kind="monthly_day",
                                   day_of_month=15, start_time=time(9), end_time=time(12))
    ds = sched._dates_for_template(t, date(2026, 7, 1), date(2026, 9, 30))
    assert ds == [date(2026, 7, 15), date(2026, 8, 15), date(2026, 9, 15)]


def test_materialize_creates_fixed_length_slots(db):
    _weekly_template(db, weekday=2)
    rep = sched.materialize_pellet_slots(db, days_ahead=20, today=date(2026, 6, 30))
    db.commit()
    slots = db.query(PelletSlot).filter(PelletSlot.slot_date == date(2026, 7, 1)).all()
    assert len(slots) == 3
    assert {s.start_time for s in slots} == {time(9, 0), time(10, 0), time(11, 0)}
    assert rep["created"] >= 3


def test_materialize_idempotent(db):
    _weekly_template(db, weekday=2)
    sched.materialize_pellet_slots(db, days_ahead=20, today=date(2026, 6, 30)); db.commit()
    before = db.query(PelletSlot).count()
    sched.materialize_pellet_slots(db, days_ahead=20, today=date(2026, 6, 30)); db.commit()
    assert db.query(PelletSlot).count() == before


def test_materialize_does_not_touch_booked_slots(db):
    _weekly_template(db, weekday=2)
    sched.materialize_pellet_slots(db, days_ahead=20, today=date(2026, 6, 30)); db.commit()
    s = db.query(PelletSlot).filter(PelletSlot.start_time == time(9, 0)).first()
    s.status = "booked"; db.commit()
    sched.materialize_pellet_slots(db, days_ahead=20, today=date(2026, 6, 30)); db.commit()
    db.refresh(s)
    assert s.status == "booked"
