"""Reputation module schema — 4 tables."""
from datetime import datetime, timedelta
from app.models.reputation import (
    ReputationProfile, ReputationScan, ReputationReview,
    ReputationPhoneChallenge,
)


def test_profile_round_trip(db):
    p = ReputationProfile(display_name="Sarah Smith, RN",
                              role_label="Surgical Coordinator",
                              qr_token="abc123def456")
    db.add(p); db.commit(); db.refresh(p)
    assert p.id is not None
    assert p.active is True
    assert p.user_email is None
    assert p.created_at is not None


def test_profile_qr_token_unique(db):
    db.add(ReputationProfile(display_name="A", qr_token="dup"))
    db.commit()
    db.add(ReputationProfile(display_name="B", qr_token="dup"))
    import pytest
    with pytest.raises(Exception):
        db.commit()


def test_scan_round_trip(db):
    p = ReputationProfile(display_name="Sarah", qr_token="t1")
    db.add(p); db.commit(); db.refresh(p)
    s = ReputationScan(profile_id=p.id, ip_address="1.2.3.4",
                            user_agent="curl/8.0", points_credited=1)
    db.add(s); db.commit(); db.refresh(s)
    assert s.scanned_at is not None
    assert s.points_credited == 1


def test_review_round_trip_with_chart_link(db):
    p = ReputationProfile(display_name="Sarah", qr_token="t2")
    db.add(p); db.commit(); db.refresh(p)
    r = ReputationReview(
        profile_id=p.id, stars=5, body="Great care!",
        patient_first_name="Jane", patient_last_initial="D",
        patient_chart_number="12345", patient_phone="+12405551234",
        consent_to_display=True,
    )
    db.add(r); db.commit(); db.refresh(r)
    assert r.stars == 5
    assert r.consent_to_display is True
    assert r.approved_for_embed is False
    assert r.google_clicked_at is None
    assert r.submitted_at is not None


def test_review_anonymous_defaults(db):
    p = ReputationProfile(display_name="Sarah", qr_token="t3")
    db.add(p); db.commit(); db.refresh(p)
    r = ReputationReview(profile_id=p.id, stars=4)
    db.add(r); db.commit(); db.refresh(r)
    assert r.patient_first_name is None
    assert r.patient_chart_number is None
    assert r.consent_to_display is False
    assert r.approved_for_embed is False


def test_phone_challenge_round_trip(db):
    c = ReputationPhoneChallenge(
        challenge_token="t-abc",
        code_hash="$2b$12$fake.hash",
        phone="+12405551234",
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db.add(c); db.commit(); db.refresh(c)
    assert c.id is not None
    assert c.created_at is not None
