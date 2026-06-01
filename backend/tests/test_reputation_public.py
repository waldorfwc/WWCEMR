"""Public review-form endpoints — no auth."""
from datetime import date
from unittest.mock import patch


def _seed_profile(db, token="abc12345"):
    from app.models.reputation import ReputationProfile
    p = ReputationProfile(display_name="Sarah, RN",
                              role_label="Coordinator", qr_token=token)
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_scan_logs_scan_and_returns_profile_info(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/scan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Sarah, RN"
    assert body["role_label"] == "Coordinator"
    from app.models.reputation import ReputationScan
    scans = db.query(ReputationScan).filter(
        ReputationScan.profile_id == p.id).all()
    assert len(scans) == 1
    assert scans[0].points_credited == 1


def test_scan_dedup_within_24h_same_ip(client, db):
    p = _seed_profile(db)
    h = {"X-Forwarded-For": "1.2.3.4"}
    r1 = client.post(f"/api/r/{p.qr_token}/scan", headers=h)
    r2 = client.post(f"/api/r/{p.qr_token}/scan", headers=h)
    assert r1.status_code == 200 and r2.status_code == 200
    from app.models.reputation import ReputationScan
    scans = db.query(ReputationScan).filter(
        ReputationScan.profile_id == p.id).all()
    assert len(scans) == 2
    assert sum(s.points_credited for s in scans) == 1


def test_scan_unknown_token_returns_404(client, db):
    r = client.post("/api/r/no-such-token/scan")
    assert r.status_code == 404


def test_scan_inactive_profile_returns_404(client, db):
    p = _seed_profile(db, token="inact")
    p.active = False
    db.commit()
    r = client.post(f"/api/r/{p.qr_token}/scan")
    assert r.status_code == 404


def test_submit_review_anonymous_persists(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/submit",
                       json={"stars": 4, "body": "Good visit"})
    assert r.status_code == 200, r.text
    from app.models.reputation import ReputationReview
    reviews = db.query(ReputationReview).filter(
        ReputationReview.profile_id == p.id).all()
    assert len(reviews) == 1
    assert reviews[0].stars == 4
    assert reviews[0].body == "Good visit"
    assert reviews[0].patient_first_name is None
    assert reviews[0].consent_to_display is False


def test_submit_review_rejects_bad_stars(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/submit", json={"stars": 7})
    assert r.status_code == 422


def test_submit_review_with_consent_requires_first_name(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/submit", json={
        "stars": 5, "consent_to_display": True,
    })
    assert r.status_code == 422
    assert "name" in r.json()["detail"].lower()


def test_submit_review_offer_google_when_5_star(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/submit", json={"stars": 5})
    assert r.status_code == 200
    assert r.json()["offer_google_handoff"] is True


def test_submit_review_no_google_offer_when_below_5(client, db):
    p = _seed_profile(db)
    r = client.post(f"/api/r/{p.qr_token}/submit", json={"stars": 3})
    assert r.status_code == 200
    assert r.json()["offer_google_handoff"] is False


def test_google_clicked_marks_timestamp(client, db):
    p = _seed_profile(db)
    sub = client.post(f"/api/r/{p.qr_token}/submit",
                          json={"stars": 5}).json()
    r = client.post(f"/api/r/{p.qr_token}/google-clicked",
                       json={"review_id": sub["review_id"]})
    assert r.status_code == 200
    from app.models.reputation import ReputationReview
    rv = db.query(ReputationReview).filter(
        ReputationReview.id == sub["review_id"]).first()
    assert rv.google_clicked_at is not None


def test_verify_patient_start_dispatches_sms(client, db):
    p = _seed_profile(db)
    with patch("app.routers.reputation_public.send_sms",
                return_value=True) as mock_sms:
        r = client.post(f"/api/r/{p.qr_token}/verify-patient/start",
                          json={"phone": "+12405551234"})
    assert r.status_code == 200
    assert "challenge_token" in r.json()
    assert mock_sms.called
    sms_to, sms_body = mock_sms.call_args[0]
    assert sms_to == "+12405551234"
    assert "review" in sms_body.lower() or "code" in sms_body.lower()


def test_verify_patient_check_matches_chart_when_phone_matches(client, db):
    from app.models.surgery import Surgery
    s = Surgery(chart_number="C-9001", patient_name="Jane Doe",
                  status="new", version_id=1,
                  cell_phone="+12405551234")
    db.add(s); db.commit()
    p = _seed_profile(db)
    with patch("app.routers.reputation_public._generate_code",
                return_value="111111"), \
         patch("app.routers.reputation_public.send_sms", return_value=True):
        start = client.post(
            f"/api/r/{p.qr_token}/verify-patient/start",
            json={"phone": "+12405551234"}).json()
    r = client.post(f"/api/r/{p.qr_token}/verify-patient/check", json={
        "challenge_token": start["challenge_token"], "code": "111111",
    })
    assert r.status_code == 200
    assert r.json()["chart_number"] == "C-9001"


def test_verify_patient_check_rejects_bad_code(client, db):
    p = _seed_profile(db)
    with patch("app.routers.reputation_public._generate_code",
                return_value="111111"), \
         patch("app.routers.reputation_public.send_sms", return_value=True):
        start = client.post(
            f"/api/r/{p.qr_token}/verify-patient/start",
            json={"phone": "+12405551234"}).json()
    r = client.post(f"/api/r/{p.qr_token}/verify-patient/check", json={
        "challenge_token": start["challenge_token"], "code": "000000",
    })
    assert r.status_code == 401
