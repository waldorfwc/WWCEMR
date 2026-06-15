"""Per-day work-assignment designation endpoint (GET /surgery/calendar/day-types)."""
from datetime import date, time

from app.models.surgery import BlockDay, SurgeryBlackoutDay

# 2026-06-15 Mon … 2026-06-21 Sun (06-20 Sat, 06-21 Sun).
START = "2026-06-15"
END = "2026-06-21"


def _seed(db):
    # MedStar block day on Mon 06-15.
    db.add(BlockDay(facility="medstar", block_date=date(2026, 6, 15),
                    block_kind="robotic_180",
                    start_time=time(7, 0), end_time=time(15, 0)))
    # Office block day on Tue 06-16.
    db.add(BlockDay(facility="office", block_date=date(2026, 6, 16),
                    block_kind="office",
                    start_time=time(9, 0), end_time=time(12, 0)))
    # Whole-day PTO blackout on Wed 06-17.
    db.add(SurgeryBlackoutDay(blackout_date=date(2026, 6, 17),
                              scope="provider", reason="pto",
                              start_time=None, end_time=None))
    db.commit()


def _types(client):
    resp = client.get("/api/surgery/calendar/day-types",
                      params={"start": START, "end": END})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_day_types_designations(client, db):
    _seed(db)
    body = _types(client)

    # Every date in the inclusive range is present.
    assert set(body) == {f"2026-06-{d:02d}" for d in range(15, 22)}

    # MedStar block day.
    assert body["2026-06-15"]["type"] == "medstar"
    assert body["2026-06-15"]["label"] == "MedStar"
    assert body["2026-06-15"]["facilities"] == ["medstar"]

    # Office block day.
    assert body["2026-06-16"]["type"] == "office_procedures"
    assert body["2026-06-16"]["label"] == "Office Procedures"
    assert body["2026-06-16"]["facilities"] == ["office"]

    # Whole-day PTO blackout.
    assert body["2026-06-17"]["type"] == "blocked"
    assert body["2026-06-17"]["label"] == "PTO"
    assert body["2026-06-17"]["reason"] == "pto"

    # Plain working weekday (Thu) — no block, no blackout.
    assert body["2026-06-18"]["type"] == "office_patients"
    assert body["2026-06-18"]["label"] == "Office Patients"

    # Weekend.
    assert body["2026-06-20"]["type"] == "none"
    assert body["2026-06-20"]["label"] is None
    assert body["2026-06-21"]["type"] == "none"


def test_day_types_blackout_label_falls_back_to_custom_label(client, db):
    db.add(SurgeryBlackoutDay(blackout_date=date(2026, 6, 18),
                              scope="office", reason="holiday",
                              label="Juneteenth",
                              start_time=None, end_time=None))
    db.commit()
    body = _types(client)
    assert body["2026-06-18"]["type"] == "blocked"
    assert body["2026-06-18"]["label"] == "Juneteenth"
    assert body["2026-06-18"]["reason"] == "holiday"


def test_day_types_partial_blackout_does_not_override(client, db):
    # Partial-day blackout on a plain weekday → still office_patients but
    # annotated with partial_block_reason.
    db.add(SurgeryBlackoutDay(blackout_date=date(2026, 6, 18),
                              scope="facility", reason="equipment_down",
                              start_time=time(13, 0), end_time=time(15, 0)))
    db.commit()
    body = _types(client)
    assert body["2026-06-18"]["type"] == "office_patients"
    assert body["2026-06-18"]["partial_block_reason"] == "equipment_down"


def test_day_types_mixed_facilities(client, db):
    db.add(BlockDay(facility="medstar", block_date=date(2026, 6, 19),
                    block_kind="robotic_180",
                    start_time=time(7, 0), end_time=time(12, 0)))
    db.add(BlockDay(facility="office", block_date=date(2026, 6, 19),
                    block_kind="office",
                    start_time=time(13, 0), end_time=time(16, 0)))
    db.commit()
    body = _types(client)
    assert body["2026-06-19"]["type"] == "mixed"
    assert body["2026-06-19"]["label"] == "MedStar + Office Procedures"
    assert body["2026-06-19"]["facilities"] == ["medstar", "office"]


def test_day_types_missing_param_422(client):
    resp = client.get("/api/surgery/calendar/day-types",
                      params={"start": START})
    assert resp.status_code == 422


def test_day_types_invalid_date_422(client):
    resp = client.get("/api/surgery/calendar/day-types",
                      params={"start": "nope", "end": END})
    assert resp.status_code == 422


def test_day_types_range_cap_422(client):
    resp = client.get("/api/surgery/calendar/day-types",
                      params={"start": "2026-01-01", "end": "2026-12-31"})
    assert resp.status_code == 422
