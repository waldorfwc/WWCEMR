from datetime import date
import pytest
from app.models.pellet import PelletPatient, PelletVisit, PelletVisitDose, PelletDoseType
from app.services.pellet import portal_auth


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


def test_info_returns_config_text(client, db, auth):
    _p, h = auth
    body = client.get("/api/pellet-portal/info", headers=h).json()
    assert "info_text" in body and isinstance(body["info_text"], str) and len(body["info_text"]) > 0


def test_config_roundtrips_portal_info_text(client, db):
    r = client.put("/api/pellets/config", json={"portal_info_text": "## Rules\nBe within 1 year."})
    assert r.status_code == 200, r.text
    assert client.get("/api/pellets/config").json()["portal_info_text"] == "## Rules\nBe within 1 year."


def test_appointments_lists_visits_with_dosage(client, db, auth):
    p, h = auth
    dt = PelletDoseType(hormone="estradiol", dose_mg=12.5, label="Estradiol 12.5mg")
    db.add(dt); db.flush()
    v = PelletVisit(patient_id=p.id, visit_kind="repeat", status="inserted",
                    scheduled_date=date(2026, 5, 1), location="white_plains",
                    provider="Cooke, Aryian, MD")
    db.add(v); db.flush()
    db.add(PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2))
    db.commit()
    items = client.get("/api/pellet-portal/appointments", headers=h).json()["items"]
    assert len(items) == 1
    a = items[0]
    assert a["location"] == "white_plains" and a["provider"] == "Cooke, Aryian, MD"
    assert a["status"] == "inserted" and a["scheduled_date"] == "2026-05-01"
    assert a["doses"] == [{"label": "Estradiol 12.5mg", "quantity": 2}]


def test_appointments_empty_for_new_patient(client, db, auth):
    _p, h = auth
    assert client.get("/api/pellet-portal/appointments", headers=h).json()["items"] == []
