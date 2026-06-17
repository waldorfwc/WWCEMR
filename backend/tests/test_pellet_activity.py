from datetime import date
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletActivity
from app.services.pellet.activity import record_pellet_activity


def _patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_record_and_list_feed(client, db):
    p = _patient(db)
    record_pellet_activity(db, p, "mammo_uploaded", "Uploaded mammogram")
    db.commit()
    body = client.get("/api/pellets/activity").json()
    assert body["items"][0]["kind"] == "mammo_uploaded"
    assert body["items"][0]["patient_name"] == "Doe, Jane"


def test_unread_count_and_read_all(client, db):
    p = _patient(db)
    record_pellet_activity(db, p, "labs_self_reported", "Self-reported labs")
    db.commit()
    assert client.get("/api/pellets/activity/unread-count").json()["count"] == 1
    client.post("/api/pellets/activity/read-all")
    assert client.get("/api/pellets/activity/unread-count").json()["count"] == 0


def test_verify_checkoff_sets_flag(client, db):
    p = _patient(db)
    record_pellet_activity(db, p, "mammo_uploaded", "Uploaded mammogram")
    db.commit()
    act_id = client.get("/api/pellets/activity").json()["items"][0]["id"]
    r = client.post(f"/api/pellets/activity/{act_id}/verify")
    assert r.status_code == 200
    db.refresh(p)
    assert p.mammo_verified is True
    assert p.mammo_verified_by


def test_verify_labs_checkoff(client, db):
    p = _patient(db)
    record_pellet_activity(db, p, "labs_self_reported", "Self-reported labs")
    db.commit()
    act_id = client.get("/api/pellets/activity").json()["items"][0]["id"]
    client.post(f"/api/pellets/activity/{act_id}/verify")
    db.refresh(p)
    assert p.labs_verified is True
