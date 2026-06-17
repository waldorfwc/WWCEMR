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


def test_token_roundtrip(db, patient):
    tok = portal_auth.issue_portal_token(patient)
    claims = portal_auth.decode_portal_token(tok)
    assert claims["pellet_patient_id"] == str(patient.id)
    assert claims["ppv"] == (patient.portal_token_version or 0)


def test_login_then_verify(client, db, patient, monkeypatch):
    sent = {}
    monkeypatch.setattr(portal_auth, "_send_sms",
                        lambda phone, body: sent.update(phone=phone, body=body))
    r = client.post("/api/pellet-portal/login",
                    json={"dob": "1980-05-01", "last4": "1234"})
    assert r.status_code == 200, r.text
    ct = r.json()["challenge_token"]
    code = sent["body"].split()[-1]
    r2 = client.post("/api/pellet-portal/verify",
                     json={"challenge_token": ct, "code": code})
    assert r2.status_code == 200, r2.text
    assert "token" in r2.json()


def test_verify_bad_code(client, db, patient, monkeypatch):
    monkeypatch.setattr(portal_auth, "_send_sms", lambda *a, **k: None)
    ct = client.post("/api/pellet-portal/login",
                     json={"dob": "1980-05-01", "last4": "1234"}).json()["challenge_token"]
    r = client.post("/api/pellet-portal/verify",
                    json={"challenge_token": ct, "code": "000000"})
    assert r.status_code in (400, 401)


def test_require_token_rejects_missing(client):
    # A protected route doesn't exist yet (T4), but decode rejects bad tokens.
    assert portal_auth.decode_portal_token("garbage") is None
