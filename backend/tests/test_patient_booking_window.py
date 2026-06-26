from datetime import date, timedelta
from app.models.surgery_config import SurgeryConfig
from app.services.surgery.date_picker import (
    patient_min_pickable_date, patient_max_pickable_date,
)


def _set(db, key, value):
    db.add(SurgeryConfig(key=key, value=value)); db.commit()


# ── earliest-bookable-date floor ──────────────────────────────────────
def test_earliest_booking_date_raises_floor(db):
    far = (date.today() + timedelta(days=120)).isoformat()
    _set(db, "patient_earliest_booking_date", far)
    assert patient_min_pickable_date(db) == date.fromisoformat(far)


def test_past_earliest_date_does_not_lower_floor(db):
    _set(db, "patient_earliest_booking_date", "2020-01-01")
    assert patient_min_pickable_date(db) > date(2020, 1, 1)   # 5-biz-day rule wins


def test_no_earliest_date_uses_business_day_rule(db):
    assert patient_min_pickable_date(db) > date.today()       # ~5 business days out


# ── booking-window ceiling ────────────────────────────────────────────
def test_booking_window_caps_max_date(db):
    _set(db, "patient_booking_window_days", 90)
    assert patient_max_pickable_date(db) == date.today() + timedelta(days=90)


def test_booking_window_default_180(db):
    assert patient_max_pickable_date(db) == date.today() + timedelta(days=180)


# ── settings persistence via the config endpoint ──────────────────────
def test_config_persists_both_settings(client, db):
    sep = (date.today() + timedelta(days=60)).isoformat()
    r = client.put("/api/surgery/config", json={
        "patient_booking_window_days": 90,
        "patient_earliest_booking_date": sep})
    assert r.status_code == 200, r.text
    got = client.get("/api/surgery/config").json()
    assert got["patient_booking_window_days"] == 90
    assert got["patient_earliest_booking_date"] == sep


def test_config_rejects_bad_earliest_date(client, db):
    r = client.put("/api/surgery/config",
                   json={"patient_earliest_booking_date": "not-a-date"})
    assert r.status_code == 422


def test_config_clears_earliest_date_with_null(client, db):
    _set(db, "patient_earliest_booking_date", "2030-01-01")
    r = client.put("/api/surgery/config",
                   json={"patient_earliest_booking_date": None})
    assert r.status_code == 200, r.text
    assert client.get("/api/surgery/config").json()["patient_earliest_booking_date"] in (None, "")
