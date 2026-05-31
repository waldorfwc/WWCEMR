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
    assert "phone" not in r.text.lower()


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
    assert "15 minutes" in r.text
    assert "240-252-2140" in r.text


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


def test_verify_returns_token_on_correct_code(client, db):
    s = _seed_surgery(db)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            login = client.post("/api/patient/portal/login",
                                  json={"dob": "1990-01-01",
                                          "phone_last4": "1234"}).json()
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": login["challenge_token"],
                              "code": "111111"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body and body["token"].count(".") == 2  # JWT shape
    assert body["surgery_id"] == str(s.id)
    assert "expires_at" in body and "T" in body["expires_at"]


def test_verify_rejects_wrong_code(client, db):
    _seed_surgery(db)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            login = client.post("/api/patient/portal/login",
                                  json={"dob": "1990-01-01",
                                          "phone_last4": "1234"}).json()
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": login["challenge_token"],
                              "code": "000000"})
    assert r.status_code == 401


def test_verify_rejects_unknown_challenge(client, db):
    _seed_surgery(db)
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": "not-real", "code": "111111"})
    assert r.status_code == 401


def test_verify_rejects_replay_of_correct_code(client, db):
    _seed_surgery(db)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            login = client.post("/api/patient/portal/login",
                                  json={"dob": "1990-01-01",
                                          "phone_last4": "1234"}).json()
    # First call succeeds.
    r1 = client.post("/api/patient/portal/verify",
                      json={"challenge_token": login["challenge_token"],
                                "code": "111111"})
    assert r1.status_code == 200
    # Replay with same code + same challenge_token must be rejected.
    r2 = client.post("/api/patient/portal/verify",
                      json={"challenge_token": login["challenge_token"],
                                "code": "111111"})
    assert r2.status_code == 401


def test_verify_kills_challenge_after_three_wrong_codes(client, db):
    _seed_surgery(db)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"):
        with patch("app.services.patient_portal_auth.send_sms",
                    return_value=True):
            login = client.post("/api/patient/portal/login",
                                  json={"dob": "1990-01-01",
                                          "phone_last4": "1234"}).json()
    ch = login["challenge_token"]
    for _ in range(3):
        r = client.post("/api/patient/portal/verify",
                         json={"challenge_token": ch, "code": "000000"})
        assert r.status_code == 401
    # Even the correct code is now refused — challenge is dead.
    r = client.post("/api/patient/portal/verify",
                     json={"challenge_token": ch, "code": "111111"})
    assert r.status_code == 401


def test_dashboard_requires_token(client, db):
    s = _seed_surgery(db)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard")
    assert r.status_code == 401


def test_dashboard_returns_surgery_and_milestones(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from datetime import date as _d
    s = Surgery(
        chart_number="1", patient_name="Doe, Jane", first_name="Jane",
        cell_phone="+12405551234", dob=_d(1990, 1, 1),
        scheduled_date=_d(2026, 6, 15),
        eligible_facilities=["office"], selected_facility="office",
        procedures=[{"cpt": "58558", "description": "Hysteroscopy with D&C"}],
        patient_responsibility=250,
        status="confirmed",
    )
    db.add(s); db.commit(); db.refresh(s)
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/dashboard",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Surgery summary
    assert body["surgery"]["procedure"] == "Hysteroscopy with D&C"
    assert body["surgery"]["surgery_date"] == "2026-06-15"
    assert body["surgery"]["facility"] == "the office"  # FACILITY_SHORT
    assert body["surgery"]["patient_responsibility"] == 250
    # Milestones — list of {key, label, status, ...}
    keys = [m["key"] for m in body["milestones"]]
    assert "payment" in keys
    assert "schedule" in keys
    assert "consent" in keys
    # Next-thing banner
    assert "next_action" in body


def test_dashboard_rejects_token_for_different_surgery(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s1 = _seed_surgery(db, cell="+12405551111", dob=date(1990, 1, 1))
    s2 = _seed_surgery(db, cell="+12405552222", dob=date(1992, 2, 2))
    token = issue_portal_token(s1)
    r = client.get(f"/api/patient/portal/{s2.id}/dashboard",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
