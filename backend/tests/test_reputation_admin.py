"""Admin reputation endpoints — profiles CRUD + leaderboard + moderation."""
from datetime import datetime


def test_create_profile_mints_qr_token(client, db):
    r = client.post("/api/admin/reputation/profiles", json={
        "display_name": "Sarah Smith, RN",
        "role_label": "Coordinator",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Sarah Smith, RN"
    assert body["role_label"] == "Coordinator"
    assert body["active"] is True
    assert len(body["qr_token"]) >= 10   # urlsafe(12) produces ~16 chars


def test_list_profiles_sorted_active_first_then_name(client, db):
    from app.models.reputation import ReputationProfile
    db.add_all([
        ReputationProfile(display_name="Zach", qr_token="t1", active=False),
        ReputationProfile(display_name="Alice", qr_token="t2", active=True),
        ReputationProfile(display_name="Mike", qr_token="t3", active=True),
    ])
    db.commit()
    r = client.get("/api/admin/reputation/profiles")
    assert r.status_code == 200
    names = [p["display_name"] for p in r.json()["profiles"]]
    # active first (alphabetical within), then inactive
    assert names == ["Alice", "Mike", "Zach"]


def test_patch_profile_updates_fields(client, db):
    from app.models.reputation import ReputationProfile
    p = ReputationProfile(display_name="Old", qr_token="t1")
    db.add(p); db.commit(); db.refresh(p)
    r = client.patch(f"/api/admin/reputation/profiles/{p.id}",
                        json={"display_name": "New", "active": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "New"
    assert body["active"] is False


def test_rotate_token_mints_new_token(client, db):
    from app.models.reputation import ReputationProfile
    p = ReputationProfile(display_name="X", qr_token="original-token")
    db.add(p); db.commit(); db.refresh(p)
    r = client.post(f"/api/admin/reputation/profiles/{p.id}/rotate-token")
    assert r.status_code == 200
    new_token = r.json()["qr_token"]
    assert new_token != "original-token"
    db.refresh(p)
    assert p.qr_token == new_token


def test_leaderboard_zero_state(client, db):
    from app.models.reputation import ReputationProfile
    p = ReputationProfile(display_name="Sarah", qr_token="t1")
    db.add(p); db.commit(); db.refresh(p)
    r = client.get("/api/admin/reputation/leaderboard")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["display_name"] == "Sarah"
    assert row["points"] == 0
    assert row["review_count"] == 0


def test_leaderboard_computes_points_correctly(client, db):
    from app.models.reputation import (
        ReputationProfile, ReputationScan, ReputationReview,
    )
    p = ReputationProfile(display_name="Sarah", qr_token="t1")
    db.add(p); db.commit(); db.refresh(p)
    # 2 scans, 1 credited (other was dedup) → 1 scan_point
    db.add(ReputationScan(profile_id=p.id, points_credited=1))
    db.add(ReputationScan(profile_id=p.id, points_credited=0))
    # 3 reviews: one 5-star with Google click, one 5-star no click, one 4-star
    db.add(ReputationReview(profile_id=p.id, stars=5,
                                  google_clicked_at=datetime.utcnow()))
    db.add(ReputationReview(profile_id=p.id, stars=5))
    db.add(ReputationReview(profile_id=p.id, stars=4))
    db.commit()
    r = client.get("/api/admin/reputation/leaderboard")
    row = r.json()["rows"][0]
    assert row["scan_points"] == 1
    assert row["review_count"] == 3
    assert row["five_star_count"] == 2
    assert row["google_share_count"] == 1
    # 1 (scan) + 3*2 (reviews) + 2*5 (5-star) + 1*3 (google) = 20
    assert row["points"] == 20


def test_leaderboard_sorted_by_points_desc(client, db):
    from app.models.reputation import ReputationProfile, ReputationReview
    p1 = ReputationProfile(display_name="High", qr_token="t1")
    p2 = ReputationProfile(display_name="Low", qr_token="t2")
    db.add_all([p1, p2]); db.commit(); db.refresh(p1); db.refresh(p2)
    db.add(ReputationReview(profile_id=p1.id, stars=5))
    db.add(ReputationReview(profile_id=p1.id, stars=5))
    db.add(ReputationReview(profile_id=p2.id, stars=3))
    db.commit()
    rows = client.get("/api/admin/reputation/leaderboard").json()["rows"]
    assert rows[0]["display_name"] == "High"
    assert rows[1]["display_name"] == "Low"


def test_list_reviews_includes_phi(client, db):
    """Admin reviews list MUST include chart_number + phone for staff use."""
    from app.models.reputation import ReputationProfile, ReputationReview
    p = ReputationProfile(display_name="Sarah", qr_token="t1")
    db.add(p); db.commit(); db.refresh(p)
    r = ReputationReview(profile_id=p.id, stars=5,
                              patient_first_name="Jane",
                              patient_chart_number="C-999",
                              patient_phone="+12405551234",
                              consent_to_display=True)
    db.add(r); db.commit()
    resp = client.get("/api/admin/reputation/reviews")
    assert resp.status_code == 200
    rev = resp.json()["reviews"][0]
    assert rev["patient_chart_number"] == "C-999"
    # patient_first_name visible
    assert rev["patient_first_name"] == "Jane"
    # profile name joined in
    assert rev["profile_display_name"] == "Sarah"


def test_patch_review_approves_for_embed(client, db):
    from app.models.reputation import ReputationProfile, ReputationReview
    p = ReputationProfile(display_name="Sarah", qr_token="t1")
    db.add(p); db.commit(); db.refresh(p)
    r = ReputationReview(profile_id=p.id, stars=5)
    db.add(r); db.commit(); db.refresh(r)
    assert r.approved_for_embed is False
    resp = client.patch(f"/api/admin/reputation/reviews/{r.id}",
                            json={"approved_for_embed": True})
    assert resp.status_code == 200
    db.refresh(r)
    assert r.approved_for_embed is True
