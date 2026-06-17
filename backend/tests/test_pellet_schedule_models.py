from datetime import date, time
from app.models.pellet_schedule import PelletAvailabilityTemplate, PelletSlot


def test_template_row(db):
    t = PelletAvailabilityTemplate(location="white_plains", recurrence_kind="weekly",
                                   weekday=0, start_time=time(9, 0), end_time=time(12, 0),
                                   slot_minutes=60, active=True)
    db.add(t); db.commit(); db.refresh(t)
    assert t.recurrence_kind == "weekly" and t.weekday == 0


def test_slot_row(db):
    s = PelletSlot(location="white_plains", slot_date=date(2026, 7, 1),
                   start_time=time(9, 0), end_time=time(10, 0), status="open")
    db.add(s); db.commit(); db.refresh(s)
    assert s.status == "open" and s.pellet_visit_id is None
