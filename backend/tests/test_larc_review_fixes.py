import hashlib
from datetime import date

from app.models.larc import LarcAssignment, LarcDeviceType, LarcPortalAuthAttempt
from app.models.patient_email import PatientEmail
from app.services.larc import portal_auth


def _a(db, flow="pharmacy_order"):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="RF1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow=flow, status="in_progress", patient_email="p@example.com",
                       patient_dob=date(1990, 5, 1), patient_cell="240-555-0199", is_active=True)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_pharmacy_benefits_does_not_send_responsibility_due(client, db):
    a = _a(db, flow="pharmacy_order")
    r = client.post(f"/api/larc/assignments/{a.id}/benefits", json={
        "allowed_amount": 900, "deductible": 0, "deductible_met": 0, "copay": 0,
        "coinsurance_pct": 0, "oop_max": 0, "oop_met": 0})
    assert r.status_code == 200, r.text
    assert db.query(PatientEmail).filter(
        PatientEmail.larc_assignment_id == a.id,
        PatientEmail.template_kind == "larc_responsibility_due").count() == 0


def test_instock_benefits_does_send_responsibility_due(client, db):
    a = _a(db, flow="in_stock")
    r = client.post(f"/api/larc/assignments/{a.id}/benefits", json={
        "allowed_amount": 900, "deductible": 500, "deductible_met": 0, "copay": 0,
        "coinsurance_pct": 0, "oop_max": 5000, "oop_met": 0})
    assert r.status_code == 200, r.text
    assert db.query(PatientEmail).filter(
        PatientEmail.larc_assignment_id == a.id,
        PatientEmail.template_kind == "larc_responsibility_due").count() == 1


def test_portal_verify_optin_sets_consent(client, db):
    a = _a(db)
    assert not a.sms_consent
    # Drive the real challenge → verify flow. issue_challenge stores a hashed
    # code (the plaintext only goes out by SMS), so overwrite the attempt row's
    # code_hash with a known code, then POST /verify with that code + opt-in.
    ct = portal_auth.issue_challenge(db, a, purpose="login")
    att = (db.query(LarcPortalAuthAttempt)
             .filter(LarcPortalAuthAttempt.challenge_token == ct).first())
    att.code_hash = hashlib.sha256(b"123456").hexdigest()
    db.commit()
    r = client.post("/api/larc-portal/verify", json={
        "challenge_token": ct, "code": "123456", "sms_opt_in": True})
    assert r.status_code == 200, r.text
    db.refresh(a)
    assert a.sms_consent is True


def test_token_revocation_rejected(client, db):
    a = _a(db)
    tok = portal_auth.issue_portal_token(a)
    a.portal_token_version = 5; db.commit()
    r = client.get("/api/larc-portal/dashboard", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401
