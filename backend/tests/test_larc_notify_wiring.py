from app.models.larc import LarcAssignment, LarcDeviceType
from app.models.patient_email import PatientEmail


def _a(db, flow="in_stock"):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="W1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow=flow, status="in_progress", patient_email="p@example.com")
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_benefits_fires_responsibility_notice(client, db):
    a = _a(db)
    r = client.post(f"/api/larc/assignments/{a.id}/benefits", json={
        "allowed_amount": 900, "deductible": 0, "deductible_met": 0, "copay": 0,
        "coinsurance_pct": 0, "oop_max": 0, "oop_met": 0})
    assert r.status_code == 200, r.text
    assert db.query(PatientEmail).filter(
        PatientEmail.larc_assignment_id == a.id,
        PatientEmail.template_kind == "larc_responsibility_due").count() == 1


def test_notify_endpoint_fires_ready(client, db):
    a = _a(db)
    # POST /assignments/{id}/notify marks patient_notified (NotifyIn body, optional).
    r = client.post(f"/api/larc/assignments/{a.id}/notify", json={})
    assert r.status_code == 200, r.text
    assert db.query(PatientEmail).filter(
        PatientEmail.larc_assignment_id == a.id,
        PatientEmail.template_kind == "larc_ready").count() == 1
