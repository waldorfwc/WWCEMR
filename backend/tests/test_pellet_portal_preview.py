from datetime import date
import pytest
from app.models.pellet import PelletPatient
from app.services.pellet import portal_auth


@pytest.fixture
def patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_token_carries_viewer_and_short_ttl(db, patient):
    tok = portal_auth.issue_portal_token(patient, viewer="staff:s@x.com", ttl_minutes=60)
    claims = portal_auth.decode_portal_token(tok)
    assert claims["viewer"] == "staff:s@x.com"
    assert claims["pellet_patient_id"] == str(patient.id)


def test_patient_token_has_no_viewer(db, patient):
    claims = portal_auth.decode_portal_token(portal_auth.issue_portal_token(patient))
    assert "viewer" not in claims


def test_preview_token_blocks_non_get(client, db, patient):
    tok = portal_auth.issue_portal_token(patient, viewer="staff:s@x.com", ttl_minutes=60)
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/pellet-portal/dashboard", headers=h).status_code == 200
    r = client.post("/api/pellet-portal/labs", json={"completed": True}, headers=h)
    assert r.status_code == 403 and "read-only" in r.json()["detail"].lower()


def test_real_patient_token_can_act(client, db, patient):
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(patient)}"}
    assert client.post("/api/pellet-portal/labs", json={"completed": True}, headers=h).status_code == 200
