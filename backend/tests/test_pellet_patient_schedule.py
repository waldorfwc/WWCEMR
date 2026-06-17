from datetime import date, time, timedelta
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
from app.models.pellet_payment import PelletInsertionCredit
from app.models.pellet_schedule import PelletSlot
from app.services.pellet import portal_auth
from app.utils.dt import now_utc_naive


@pytest.fixture
def ready(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      mammo_verified=True, labs_verified=True)
    db.add(p); db.flush()
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="e1", status="signed",
                         signed_at=now_utc_naive(), expires_at=now_utc_naive() + timedelta(days=300)))
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    db.add(PelletSlot(location="white_plains", slot_date=date(2099, 7, 1),
                      start_time=time(9), end_time=time(10), status="open"))
    db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


def test_locations_and_open_slots(client, db, ready):
    _p, h = ready
    locs = client.get("/api/pellet-portal/schedule/locations", headers=h).json()
    assert "white_plains" in locs["locations"]
    slots = client.get("/api/pellet-portal/schedule/slots?location=white_plains", headers=h).json()
    assert len(slots["items"]) == 1 and slots["can_schedule"] is True


def test_book_and_my_bookings(client, db, ready):
    p, h = ready
    sid = client.get("/api/pellet-portal/schedule/slots?location=white_plains",
                     headers=h).json()["items"][0]["id"]
    r = client.post(f"/api/pellet-portal/schedule/slots/{sid}/book", headers=h)
    assert r.status_code == 200, r.text
    assert len(client.get("/api/pellet-portal/schedule/my", headers=h).json()["items"]) == 1


def test_book_blocked_when_gate_fails(client, db):
    p = PelletPatient(patient_name="No, Reqs", chart_number="M9",
                      patient_dob=date(1980, 5, 1), patient_phone="3015559999")
    db.add(p); db.flush()
    s = PelletSlot(location="white_plains", slot_date=date(2099, 7, 2),
                   start_time=time(9), end_time=time(10), status="open")
    db.add(s); db.commit()
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}
    r = client.post(f"/api/pellet-portal/schedule/slots/{s.id}/book", headers=h)
    assert r.status_code == 409


def test_cancel_booking(client, db, ready):
    p, h = ready
    sid = client.get("/api/pellet-portal/schedule/slots?location=white_plains",
                     headers=h).json()["items"][0]["id"]
    client.post(f"/api/pellet-portal/schedule/slots/{sid}/book", headers=h)
    r = client.post(f"/api/pellet-portal/schedule/slots/{sid}/cancel", headers=h)
    assert r.status_code == 200
    assert db.query(PelletSlot).filter(PelletSlot.id == sid).first().status == "open"


def test_dashboard_has_scheduling(client, db, ready):
    _p, h = ready
    dash = client.get("/api/pellet-portal/dashboard", headers=h).json()
    assert "scheduling" in dash and dash["scheduling"]["can_schedule"] is True
