"""Manual intake — split name, surgeon default, assistant surgeon,
clearance + device multi-selects (B3), plus the order-kind file attach (B4)."""
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
        "chart_number": "C100",
        "patient_name": "",          # client may leave blank when sending split name
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
        "surgeon_primary": "",       # blank → default
        "surgery_name": "Hysteroscopy",
        "procedures": [{"cpt": "58558", "description": "Hysteroscopy"}],
        "diagnoses": [{"icd": "N84.0", "description": "Polyp"}],
        "eligible_facilities": ["office"],
        "estimated_minutes": 60,
        "preop_date": "2026-07-01",
    }
    p.update(overrides)
    return p


def test_manual_create_split_name_and_surgeon_default(client, db):
    resp = client.post("/api/surgery/manual", json=_base_payload())
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == sid).first()
    assert s.patient_name == "Doe, Jane"
    assert s.first_name == "Jane"
    assert s.last_name == "Doe"
    assert s.surgeon_primary == "Aryian Cooke, MD"


def test_manual_create_explicit_surgeon_kept(client, db):
    resp = client.post("/api/surgery/manual",
                       json=_base_payload(surgeon_primary="Other Surgeon, MD"))
    assert resp.status_code == 201, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == resp.json()["id"]).first()
    assert s.surgeon_primary == "Other Surgeon, MD"


def test_manual_create_assistant_surgeon(client, db):
    resp = client.post("/api/surgery/manual",
                       json=_base_payload(assistant_surgeon_name="Dr. Gillespie"))
    assert resp.status_code == 201, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == resp.json()["id"]).first()
    assert s.assistant_surgeon_name == "Dr. Gillespie"
    assert s.assistant_surgeon_required is True


def test_manual_create_clearance_types(client, db):
    resp = client.post("/api/surgery/manual",
                       json=_base_payload(clearance_types=["EKG", "Cardiology"]))
    assert resp.status_code == 201, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == resp.json()["id"]).first()
    assert s.clearance_types == ["EKG", "Cardiology"]
    assert s.clearance_required is True
    assert s.clearance_status == "required"


def test_manual_create_device_types(client, db):
    resp = client.post("/api/surgery/manual",
                       json=_base_payload(device_types=["Mirena"]))
    assert resp.status_code == 201, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == resp.json()["id"]).first()
    assert s.device_types == ["Mirena"]
    assert s.device_required is True
    assert s.device_kind == "Mirena"


def test_manual_create_clearance_none_not_required(client, db):
    resp = client.post("/api/surgery/manual",
                       json=_base_payload(clearance_types=["None"]))
    assert resp.status_code == 201, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == resp.json()["id"]).first()
    assert s.clearance_types == ["None"]
    assert s.clearance_required is False
    assert s.clearance_status == "not_required"


def test_manual_create_device_none_not_required(client, db):
    resp = client.post("/api/surgery/manual",
                       json=_base_payload(device_types=["None"]))
    assert resp.status_code == 201, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == resp.json()["id"]).first()
    assert s.device_types == ["None"]
    assert s.device_required is False
    assert s.device_kind is None


def test_manual_create_assistant_none_not_required(client, db):
    resp = client.post("/api/surgery/manual",
                       json=_base_payload(assistant_surgeon_name="None"))
    assert resp.status_code == 201, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == resp.json()["id"]).first()
    assert s.assistant_surgeon_name is None
    assert s.assistant_surgeon_required is False


def test_manual_create_mixed_none_arms_only_real(client, db):
    # "None" mixed with a real selection still arms the workflow and the
    # device_kind picks the first real (non-"None") device.
    resp = client.post("/api/surgery/manual", json=_base_payload(
        clearance_types=["None", "EKG"],
        device_types=["None", "Mirena"],
    ))
    assert resp.status_code == 201, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == resp.json()["id"]).first()
    assert s.clearance_required is True
    assert s.clearance_status == "required"
    assert s.device_required is True
    assert s.device_kind == "Mirena"


def test_manual_create_payer_id_persisted(client, db):
    resp = client.post("/api/surgery/manual",
                       json=_base_payload(payer_id="60054"))
    assert resp.status_code == 201, resp.text
    from app.models.surgery import Surgery
    s = db.query(Surgery).filter(Surgery.id == resp.json()["id"]).first()
    assert s.primary_payer_id == "60054"


def test_attach_order_kind_file(client, db):
    resp = client.post("/api/surgery/manual", json=_base_payload())
    sid = resp.json()["id"]
    with patch("app.routers.surgery.save_blob",
               return_value="surgery-files/order.pdf"):
        r = client.post(
            f"/api/surgery/{sid}/files?kind=order",
            files={"file": ("order.pdf", b"%PDF-1.4 x", "application/pdf")},
        )
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "order"
    from app.models.surgery import SurgeryFile
    f = db.query(SurgeryFile).filter(SurgeryFile.surgery_id == sid).first()
    assert f is not None
    assert f.kind == "order"
