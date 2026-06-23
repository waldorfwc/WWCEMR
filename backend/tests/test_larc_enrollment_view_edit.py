import pytest
from app.models.user import User
from app.models.larc import LarcAssignment, LarcEnrollmentEnvelope
from app.services.larc.enrollment_sender import resolve_enrollment_preview


def _work_user(db):
    u = User(email="work@waldorfwomenscare.com", display_name="Work", is_super_admin=True)
    db.add(u); db.commit()
    return u


def _pharmacy_assignment(db, **over):
    a = LarcAssignment(
        chart_number="12345",
        patient_name="Jane Doe",
        source_flow="pharmacy_order",
        status="in_progress",
        patient_first_name="Jane",
        patient_last_name="Doe",
        patient_email="jane@example.com",
        primary_insurance="Aetna",
        inserting_provider_email="dr@waldorfwomenscare.com",
        inserting_provider_name="Dr. Smith",
        inserting_provider_npi="1234567890",
    )
    for k, v in over.items():
        setattr(a, k, v)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_resolve_preview_full_data_no_blanks(db):
    a = _pharmacy_assignment(db)
    out = resolve_enrollment_preview(db, a)
    labels = {f["label"]: f for f in out["fields"]}
    assert labels["Patient Name"]["value"] == "Jane Doe"
    assert labels["Patient Name"]["blank"] is False
    assert labels["Primary Insurance"]["value"] == "Aetna"
    assert out["sendable"] is True


def test_resolve_preview_flags_blanks(db):
    a = _pharmacy_assignment(db, primary_insurance=None, inserting_provider_npi=None)
    out = resolve_enrollment_preview(db, a)
    assert "Primary Insurance" in out["blanks"]
    assert "Inserting Provider NPI" in out["blanks"]


def test_resolve_preview_whitespace_override_reads_as_blank(db):
    # A whitespace-only override would be .strip()'d to empty on send and
    # fall back to PracticeConfig; the preview must agree (not show it as
    # a filled value), or it defeats the point of the preview.
    a = _pharmacy_assignment(db, inserting_provider_npi="   ")
    out = resolve_enrollment_preview(db, a)
    labels = {f["label"]: f for f in out["fields"]}
    assert labels["Inserting Provider NPI"]["value"] == ""
    assert labels["Inserting Provider NPI"]["blank"] is True
    assert "Inserting Provider NPI" in out["blanks"]


def test_resolve_preview_not_sendable_without_patient_email(db):
    a = _pharmacy_assignment(db, patient_email=None)
    out = resolve_enrollment_preview(db, a)
    assert out["sendable"] is False
    assert "Patient Email" in out["blanks"]


def test_preview_endpoint_shape_and_tier(client_factory, db):
    u = _work_user(db)
    a = _pharmacy_assignment(db)
    client = client_factory(user=u)
    r = client.get(f"/api/larc/assignments/{a.id}/enrollment/preview")
    assert r.status_code == 200
    body = r.json()
    assert "fields" in body and "blanks" in body and "sendable" in body


def test_preview_endpoint_rejects_non_pharmacy_flow(client_factory, db):
    u = _work_user(db)
    a = _pharmacy_assignment(db, source_flow="in_stock")
    client = client_factory(user=u)
    r = client.get(f"/api/larc/assignments/{a.id}/enrollment/preview")
    assert r.status_code == 400
