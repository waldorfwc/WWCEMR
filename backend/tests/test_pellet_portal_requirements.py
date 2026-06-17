from datetime import date
import io
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletActivity, PelletPortalUpload
from app.services.pellet import portal_auth


@pytest.fixture(autouse=True)
def _local_storage_root(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "documents_local_root", str(tmp_path))


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


def test_dashboard_initial_checklist(client, db, auth):
    _p, h = auth
    body = client.get("/api/pellet-portal/dashboard", headers=h).json()
    reqs = {r["key"]: r["status"] for r in body["requirements"]}
    assert reqs == {"mammo": "todo", "labs": "todo", "consent": "todo"}


def test_mammo_upload_creates_pending_and_activity(client, db, auth):
    p, h = auth
    r = client.post("/api/pellet-portal/mammo",
                    files={"file": ("m.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
                    headers=h)
    assert r.status_code == 200, r.text
    assert db.query(PelletActivity).filter(PelletActivity.kind == "mammo_uploaded").count() == 1
    assert db.query(PelletPortalUpload).count() == 1
    db.refresh(p)
    assert p.mammo_verified is False
    assert p.mammo_submitted_at is not None
    reqs = {r["key"]: r["status"] for r in
            client.get("/api/pellet-portal/dashboard", headers=h).json()["requirements"]}
    assert reqs["mammo"] == "pending"


def test_labs_self_report_creates_activity(client, db, auth):
    p, h = auth
    r = client.post("/api/pellet-portal/labs",
                    json={"completed": True, "drawn_date": "2026-06-10"}, headers=h)
    assert r.status_code == 200, r.text
    assert db.query(PelletActivity).filter(PelletActivity.kind == "labs_self_reported").count() == 1
    db.refresh(p)
    assert p.labs_verified is False
    assert p.labs_self_reported_at is not None


def test_labs_requires_completed_true(client, db, auth):
    _p, h = auth
    r = client.post("/api/pellet-portal/labs", json={"completed": False}, headers=h)
    assert r.status_code == 422


def test_endpoints_require_token(client, db):
    assert client.get("/api/pellet-portal/dashboard").status_code == 401
    assert client.post("/api/pellet-portal/labs", json={"completed": True}).status_code == 401
