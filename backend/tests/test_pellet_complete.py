from datetime import date, time, timedelta
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
from app.models.pellet_payment import PelletInsertionCredit
from app.models.pellet_schedule import PelletSlot
from app.services.pellet import scheduling as sched, payments as pay
from app.utils.dt import now_utc_naive


def _booked(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      mammo_verified=True, labs_verified=True)
    db.add(p); db.flush()
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="e1", status="signed",
                         signed_at=now_utc_naive(), expires_at=now_utc_naive() + timedelta(days=300)))
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    s = PelletSlot(location="white_plains", slot_date=date(2099, 7, 1),
                   start_time=time(9), end_time=time(10), status="open")
    db.add(s); db.commit(); db.refresh(p); db.refresh(s)
    sched.book_slot(db, slot_id=str(s.id), patient=p, by="patient"); db.commit()
    return p, s


def test_staff_complete_draws_down(client, db):
    p, s = _booked(db)
    r = client.post(f"/api/pellets/slots/{s.id}/complete")
    assert r.status_code == 200, r.text
    assert pay.credit_balance(db, p) == 0


def test_complete_409_when_no_booking(client, db):
    s = PelletSlot(location="white_plains", slot_date=date(2099, 7, 2),
                   start_time=time(9), end_time=time(10), status="open")
    db.add(s); db.commit()
    r = client.post(f"/api/pellets/slots/{s.id}/complete")
    assert r.status_code == 409
