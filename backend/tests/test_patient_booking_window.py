from datetime import date, timedelta
from app.models.surgery_config import SurgeryConfig
from app.services.surgery.date_picker import (
    patient_min_pickable_date, patient_max_pickable_date,
    patient_freeze_date_for_facility, patient_freeze_map,
)


def _set(db, key, value):
    db.add(SurgeryConfig(key=key, value=value)); db.commit()


# ── per-facility freeze ───────────────────────────────────────────────
def test_freeze_is_per_facility(db):
    _set(db, "patient_earliest_booking_date", {"medstar": "2026-09-01", "crmc": None})
    assert patient_freeze_date_for_facility(db, "medstar") == date(2026, 9, 1)
    assert patient_freeze_date_for_facility(db, "crmc") is None
    assert patient_freeze_date_for_facility(db, "office") is None


def test_legacy_string_freeze_applies_to_all_facilities(db):
    _set(db, "patient_earliest_booking_date", "2026-09-01")
    for fac in ("medstar", "crmc", "office"):
        assert patient_freeze_date_for_facility(db, fac) == date(2026, 9, 1)


def test_no_freeze_returns_none(db):
    assert patient_freeze_map(db) == {}
    assert patient_freeze_date_for_facility(db, "medstar") is None


def test_min_pickable_date_is_pure_business_days(db):
    # The 5-business-day floor is independent of the freeze now.
    _set(db, "patient_earliest_booking_date", {"medstar": "2030-01-01"})
    assert patient_min_pickable_date(db) < date(2030, 1, 1)


# ── booking-window ceiling (global) ───────────────────────────────────
def test_booking_window_caps_max_date(db):
    _set(db, "patient_booking_window_days", 90)
    assert patient_max_pickable_date(db) == date.today() + timedelta(days=90)


def test_booking_window_default_180(db):
    assert patient_max_pickable_date(db) == date.today() + timedelta(days=180)


# ── settings persistence via the config endpoint ──────────────────────
def test_config_persists_per_facility_freeze(client, db):
    r = client.put("/api/surgery/config", json={
        "patient_earliest_booking_date": {"medstar": "2026-09-01"}})
    assert r.status_code == 200, r.text
    # facility-merge: a second facility merges in, doesn't replace
    r = client.put("/api/surgery/config", json={
        "patient_earliest_booking_date": {"crmc": "2026-10-01"}})
    assert r.status_code == 200, r.text
    got = client.get("/api/surgery/config").json()["patient_earliest_booking_date"]
    assert got["medstar"] == "2026-09-01" and got["crmc"] == "2026-10-01"


def test_config_clears_one_facility(client, db):
    client.put("/api/surgery/config",
               json={"patient_earliest_booking_date": {"medstar": "2026-09-01"}})
    client.put("/api/surgery/config",
               json={"patient_earliest_booking_date": {"medstar": None}})
    got = client.get("/api/surgery/config").json()["patient_earliest_booking_date"]
    assert got.get("medstar") is None


def test_config_rejects_unknown_facility(client, db):
    r = client.put("/api/surgery/config",
                   json={"patient_earliest_booking_date": {"nope": "2026-09-01"}})
    assert r.status_code == 422


def test_config_rejects_bad_date(client, db):
    r = client.put("/api/surgery/config",
                   json={"patient_earliest_booking_date": {"medstar": "not-a-date"}})
    assert r.status_code == 422
