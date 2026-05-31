"""Portal endpoints — login + verify."""
from datetime import date
from unittest.mock import patch

from app.models.surgery import Surgery


def _seed_surgery(db, cell="+12405551234", dob=date(1990, 1, 1)):
    s = Surgery(chart_number="1", patient_name="Pat",
                  cell_phone=cell, dob=dob, status="new")
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_login_sends_sms_and_returns_challenge(client, db):
    s = _seed_surgery(db)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True) as mock_sms:
        r = client.post("/api/patient/portal/login",
                         json={"dob": "1990-01-01", "phone_last4": "1234"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "challenge_token" in body
    assert len(body["challenge_token"]) >= 32
    mock_sms.assert_called_once()


def test_login_generic_404_on_no_match(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/login",
                     json={"dob": "1980-01-01", "phone_last4": "0000"})
    assert r.status_code == 404
    # Must not reveal whether DOB or phone was wrong
    assert "dob" not in r.text.lower()
    assert "phone" not in r.text.lower() or "phone number" in r.text.lower()


def test_login_locked_out_after_three_fails(client, db):
    _seed_surgery(db)
    for _ in range(3):
        # Same DOB so the surgery is identifiable for lockout tracking,
        # but wrong last4 — so login fails and records an attempt against
        # the matched surgery id.
        client.post("/api/patient/portal/login",
                     json={"dob": "1990-01-01", "phone_last4": "0000"})
    r = client.post("/api/patient/portal/login",
                     json={"dob": "1990-01-01", "phone_last4": "1234"})
    assert r.status_code == 429


def test_login_validates_dob_format(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/login",
                     json={"dob": "not-a-date", "phone_last4": "1234"})
    assert r.status_code == 422


def test_login_validates_last4_length(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/login",
                     json={"dob": "1990-01-01", "phone_last4": "12"})
    assert r.status_code == 422
