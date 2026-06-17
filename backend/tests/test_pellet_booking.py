from datetime import date, time, timedelta
import pytest
from app.models.pellet import PelletPatient, PelletVisit
from app.models.pellet_portal import PelletConsent
from app.models.pellet_payment import PelletInsertionCredit
from app.models.pellet_schedule import PelletSlot
from app.services.pellet import scheduling as sched
from app.utils.dt import now_utc_naive


def _ready_patient(db, *, credits=1):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      mammo_verified=True, labs_verified=True)
    db.add(p); db.flush()
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="e1",
                         status="signed", signed_at=now_utc_naive(),
                         expires_at=now_utc_naive() + timedelta(days=300)))
    if credits:
        db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=credits, source="single"))
    db.commit(); db.refresh(p)
    return p


def _slot(db, d=date(2026, 7, 1), t=time(9)):
    s = PelletSlot(location="white_plains", slot_date=d,
                   start_time=t, end_time=time(t.hour + 1), status="open")
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_gate_blocks_when_requirements_incomplete(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="M2",
                      patient_dob=date(1980, 5, 1), patient_phone="3015550000")
    db.add(p); db.commit(); db.refresh(p)
    ok, reason = sched.can_schedule(db, p)
    assert ok is False


def test_gate_passes_when_ready(db):
    p = _ready_patient(db)
    ok, reason = sched.can_schedule(db, p)
    assert ok is True, reason


def test_book_links_visit_and_marks_slot(db):
    p = _ready_patient(db); s = _slot(db)
    visit = sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient"); db.commit()
    db.refresh(s)
    assert s.status == "booked" and str(s.pellet_visit_id) == str(visit.id)
    assert visit.location == "white_plains" and visit.scheduled_date == date(2026, 7, 1)


def test_book_rejects_when_no_credit(db):
    p = _ready_patient(db, credits=0); s = _slot(db)
    with pytest.raises(sched.NotEligible):
        sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient")


def test_book_rejects_taken_slot(db):
    p = _ready_patient(db); s = _slot(db); s.status = "booked"; db.commit()
    with pytest.raises(sched.SlotUnavailable):
        sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient")


def test_open_bookings_blocks_second_when_one_credit(db):
    p = _ready_patient(db, credits=1); s1 = _slot(db)
    sched.book_slot(db, slot_id=str(s1.id), patient=p, by="patient"); db.commit()
    s2 = _slot(db, d=date(2026, 7, 2))
    with pytest.raises(sched.NotEligible):
        sched.book_slot(db, slot_id=str(s2.id), patient=p, by="patient")


def test_cancel_frees_slot(db):
    p = _ready_patient(db); s = _slot(db)
    visit = sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient"); db.commit()
    sched.cancel_booking(db, slot_id=str(s.id), by="patient"); db.commit()
    db.refresh(s); db.refresh(visit)
    assert s.status == "open" and s.pellet_visit_id is None and visit.status == "cancelled"


def test_complete_draws_down_credit(db):
    from app.services.pellet import payments as pay
    p = _ready_patient(db, credits=1); s = _slot(db)
    visit = sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient"); db.commit()
    assert pay.credit_balance(db, p) == 1
    sched.complete_booking(db, slot_id=str(s.id), by="staff@x"); db.commit()
    assert pay.credit_balance(db, p) == 0
    db.refresh(visit)
    assert visit.status == "inserted"
