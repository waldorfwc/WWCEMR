"""Update-Surgery support: GET /surgery/{id} exposes all intake fields so an
edit form can prefill, and PATCH /surgery/{id} accepts the newer multi-select
list fields (clearance_types / device_types) + payer id, applying the same
derived workflow flags create_manual does."""
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _no_pg_sequence():
    # next_surgery_number() relies on a Postgres sequence (nextval) that
    # SQLite test DBs don't have. Stub the assignment for these tests.
    with patch(
        "app.services.surgery.local_helpers.maybe_assign_surgery_number",
        return_value="SUR00001",
    ):
        yield


def _base_payload(**overrides):
    p = {
        "chart_number": "C200",
        "patient_name": "",
        "first_name": "Jane",
        "last_name": "Doe",
        "dob": "1990-04-15",
        "phone": "240-555-0100",
        "email": "jane@example.com",
        "address_street": "1 Main St",
        "address_city": "Waldorf",
        "address_state": "MD",
        "address_zip": "20601",
        "primary_insurance": "Aetna",
        "primary_member_id": "A123",
        "secondary_insurance": "BCBS",
        "secondary_member_id": "B456",
        "payer_id": "60054",
        "surgeon_primary": "",
        "surgery_name": "Hysteroscopy",
        "procedures": [{"cpt": "58558", "description": "Hysteroscopy"}],
        "diagnoses": [{"icd": "N84.0", "description": "Polyp"}],
        "eligible_facilities": ["office"],
        "estimated_minutes": 60,
        "preop_date": "2026-07-01",
    }
    p.update(overrides)
    return p


def _create(client):
    resp = client.post("/api/surgery/manual", json=_base_payload())
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_get_exposes_intake_fields(client, db):
    sid = _create(client)
    resp = client.get(f"/api/surgery/{sid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("first_name", "last_name", "primary_payer_id",
                "secondary_insurance", "secondary_member_id",
                "clearance_types", "device_types"):
        assert key in body, f"missing {key} in GET response"
    assert body["first_name"] == "Jane"
    assert body["last_name"] == "Doe"
    assert body["primary_payer_id"] == "60054"
    assert body["secondary_insurance"] == "BCBS"
    assert body["secondary_member_id"] == "B456"
    assert body["clearance_types"] == []
    assert body["device_types"] == []


def test_patch_clearance_types_none_clears_required(client, db):
    sid = _create(client)
    resp = client.patch(f"/api/surgery/{sid}", json={"clearance_types": ["None"]})
    assert resp.status_code == 200, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    db.refresh(s)
    assert s.clearance_types == ["None"]
    assert s.clearance_required is False
    assert s.clearance_status == "not_required"


def test_patch_clearance_types_real_arms_required(client, db):
    sid = _create(client)
    resp = client.patch(f"/api/surgery/{sid}", json={"clearance_types": ["EKG"]})
    assert resp.status_code == 200, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    db.refresh(s)
    assert s.clearance_types == ["EKG"]
    assert s.clearance_required is True
    assert s.clearance_status == "required"
    # And it round-trips through GET
    body = client.get(f"/api/surgery/{sid}").json()
    assert body["clearance_types"] == ["EKG"]
    assert body["clearance_required"] is True


def test_patch_device_types_none_clears_required(client, db):
    sid = _create(client)
    resp = client.patch(f"/api/surgery/{sid}", json={"device_types": ["None"]})
    assert resp.status_code == 200, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    db.refresh(s)
    assert s.device_types == ["None"]
    assert s.device_required is False
    assert s.device_kind is None


def test_patch_device_types_real_arms_required(client, db):
    sid = _create(client)
    resp = client.patch(f"/api/surgery/{sid}", json={"device_types": ["Mirena"]})
    assert resp.status_code == 200, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    db.refresh(s)
    assert s.device_types == ["Mirena"]
    assert s.device_required is True
    assert s.device_kind == "Mirena"


def test_patch_assistant_surgeon_none_clears_required(client, db):
    sid = _create(client)
    # First arm it, then clear with "None".
    client.patch(f"/api/surgery/{sid}", json={"assistant_surgeon_name": "Dr. Gillespie"})
    resp = client.patch(f"/api/surgery/{sid}", json={"assistant_surgeon_name": "None"})
    assert resp.status_code == 200, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    db.refresh(s)
    assert s.assistant_surgeon_name is None
    assert s.assistant_surgeon_required is False


def test_patch_assistant_surgeon_real_arms_required(client, db):
    sid = _create(client)
    resp = client.patch(f"/api/surgery/{sid}",
                        json={"assistant_surgeon_name": "Dr. Gillespie"})
    assert resp.status_code == 200, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    db.refresh(s)
    assert s.assistant_surgeon_name == "Dr. Gillespie"
    assert s.assistant_surgeon_required is True


def test_patch_primary_payer_id_persisted(client, db):
    sid = _create(client)
    resp = client.patch(f"/api/surgery/{sid}", json={"primary_payer_id": "ABC12"})
    assert resp.status_code == 200, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    db.refresh(s)
    assert s.primary_payer_id == "ABC12"
    body = client.get(f"/api/surgery/{sid}").json()
    assert body["primary_payer_id"] == "ABC12"
