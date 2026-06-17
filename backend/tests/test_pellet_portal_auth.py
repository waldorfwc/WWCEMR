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


def test_login_rejects_non_digit_last4(client, db, patient):
    # A LIKE-wildcard like "%" must not be accepted as last4 (no SQL wildcard
    # broadening on this unauthenticated endpoint).
    r = client.post("/api/pellet-portal/login", json={"dob": "1980-05-01", "last4": "%"})
    assert r.status_code == 422


def test_match_patient_normalizes_formatted_phone(db, patient):
    # Stored phone with formatting still matches on digit-normalized last4.
    patient.patient_phone = "(301) 555-1234"
    db.commit()
    assert portal_auth.match_patient(db, date(1980, 5, 1), "1234") is not None
    # A wildcard never matches (treated as non-4-digits).
    assert portal_auth.match_patient(db, date(1980, 5, 1), "%") is None


def test_verify_burns_challenge_after_max_attempts(client, db, patient, monkeypatch):
    monkeypatch.setattr(portal_auth, "_send_sms", lambda *a, **k: None)
    ct = client.post("/api/pellet-portal/login",
                     json={"dob": "1980-05-01", "last4": "1234"}).json()["challenge_token"]
    # 5 wrong attempts burn the challenge…
    for _ in range(portal_auth._MAX_ATTEMPTS):
        assert portal_auth.verify_code(db, ct, "000000") is None
    # …so even the (unknown) right code can't be used afterward.
    from app.models.pellet_portal import PelletPortalAuthAttempt
    att = db.query(PelletPortalAuthAttempt).filter(
        PelletPortalAuthAttempt.challenge_token == ct).first()
    assert att.consumed_at is not None
