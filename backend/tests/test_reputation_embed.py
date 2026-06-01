"""Public embed endpoint — PHI-safe."""
from datetime import datetime


def _seed_profile(db, token="t1"):
    from app.models.reputation import ReputationProfile
    p = ReputationProfile(display_name="Sarah", qr_token=token)
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_embed_returns_only_consented_AND_approved(client, db):
    from app.models.reputation import ReputationReview
    p = _seed_profile(db)
    visible = ReputationReview(profile_id=p.id, stars=5,
                                     body="Great care!",
                                     patient_first_name="Jane",
                                     patient_last_initial="D",
                                     consent_to_display=True,
                                     approved_for_embed=True)
    pending = ReputationReview(profile_id=p.id, stars=4,
                                      body="Pending approval",
                                      consent_to_display=True,
                                      patient_first_name="Joe",
                                      patient_last_initial="S",
                                      approved_for_embed=False)
    no_consent = ReputationReview(profile_id=p.id, stars=5,
                                         consent_to_display=False,
                                         approved_for_embed=True)
    db.add_all([visible, pending, no_consent]); db.commit()
    r = client.get("/api/reviews/public")
    assert r.status_code == 200
    body = r.json()
    assert len(body["reviews"]) == 1
    assert body["reviews"][0]["stars"] == 5
    assert body["reviews"][0]["display_name"] == "Jane D."
    assert body["reviews"][0]["body"] == "Great care!"


def test_embed_never_exposes_phi(client, db):
    """No matter what's in DB, the embed payload must not contain
    chart_number, phone, or full last name."""
    from app.models.reputation import ReputationReview
    p = _seed_profile(db)
    r = ReputationReview(profile_id=p.id, stars=5,
                              patient_first_name="X",
                              patient_last_initial="Y",
                              patient_chart_number="C-999",
                              patient_phone="+12405551234",
                              consent_to_display=True,
                              approved_for_embed=True)
    db.add(r); db.commit()
    resp = client.get("/api/reviews/public").json()
    payload = str(resp).lower()
    assert "c-999" not in payload
    assert "+1240" not in payload
    assert "patient_chart" not in payload
    assert "patient_phone" not in payload


def test_embed_falls_back_to_anonymous_when_no_first_name(client, db):
    """consent_to_display=True without a first_name shouldn't happen
    (T2 rejects it), but if a row somehow has neither name, display
    falls back to 'Anonymous'."""
    from app.models.reputation import ReputationReview
    p = _seed_profile(db)
    r = ReputationReview(profile_id=p.id, stars=4,
                              consent_to_display=True,
                              approved_for_embed=True,
                              patient_first_name=None)
    db.add(r); db.commit()
    body = client.get("/api/reviews/public").json()
    assert body["reviews"][0]["display_name"] == "Anonymous"


def test_embed_limit_param_clamped(client, db):
    """limit=0 or negative → 1; limit > 100 → 100."""
    r = client.get("/api/reviews/public?limit=0")
    assert r.status_code == 200
    r = client.get("/api/reviews/public?limit=9999")
    assert r.status_code == 200


def test_embed_orders_newest_first(client, db):
    from app.models.reputation import ReputationReview
    p = _seed_profile(db)
    older = ReputationReview(profile_id=p.id, stars=5,
                                   body="older",
                                   patient_first_name="A",
                                   patient_last_initial="A",
                                   consent_to_display=True,
                                   approved_for_embed=True,
                                   submitted_at=datetime(2026, 1, 1, 10, 0))
    newer = ReputationReview(profile_id=p.id, stars=5,
                                   body="newer",
                                   patient_first_name="B",
                                   patient_last_initial="B",
                                   consent_to_display=True,
                                   approved_for_embed=True,
                                   submitted_at=datetime(2026, 6, 1, 10, 0))
    db.add_all([older, newer]); db.commit()
    rows = client.get("/api/reviews/public").json()["reviews"]
    assert rows[0]["body"] == "newer"
    assert rows[1]["body"] == "older"
