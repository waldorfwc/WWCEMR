"""Google Calendar sync — service contract (no real API calls)."""
from datetime import date, time, timedelta
from unittest.mock import patch, MagicMock

from app.models.surgery import Surgery, BlockDay, SurgerySlot
from app.services.google_calendar_sync import (
    _event_body, upsert_event_for_surgery, delete_event_for_surgery,
    _is_configured,
)


def _seed(db):
    s = Surgery(chart_number="1", patient_name="Pat",
                 eligible_facilities=["medstar"], selected_facility="medstar",
                 status="confirmed",
                 scheduled_date=date.today() + timedelta(days=14),
                 procedures=[{"name": "Hyst", "kind": "robotic_180"}])
    db.add(s); db.flush()
    bd = BlockDay(facility="medstar",
                   block_date=s.scheduled_date,
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(17, 0))
    db.add(bd); db.flush()
    slot = SurgerySlot(block_day_id=bd.id, surgery_id=s.id,
                        start_time=time(8, 0), duration_minutes=180,
                        procedure_kind="robotic_180")
    db.add(slot); db.commit()
    return s, slot


def test_event_body_shape(db):
    s, slot = _seed(db)
    body = _event_body(s, slot, facility_label="MedStar")
    assert body["summary"].startswith("Pat — Hyst")
    assert body["start"]["timeZone"] == "America/New_York"
    assert body["end"]["timeZone"]   == "America/New_York"
    assert body["extendedProperties"]["private"]["surgery_id"] == str(s.id)
    assert body["extendedProperties"]["private"]["slot_id"]    == str(slot.id)


def test_event_body_adds_attendee_when_surgeon_differs(db):
    s, slot = _seed(db)
    s.surgeon_email = "different@example.com"
    db.commit()
    body = _event_body(s, slot)
    assert body.get("attendees") == [{"email": "different@example.com"}]


def test_event_body_no_attendees_when_surgeon_is_owner(db, monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_OWNER_EMAIL", "owner@example.com")
    s, slot = _seed(db)
    s.surgeon_email = "owner@example.com"
    db.commit()
    body = _event_body(s, slot)
    assert "attendees" not in body


def test_upsert_softfails_when_not_configured(db, monkeypatch):
    monkeypatch.delenv("GOOGLE_WORKSPACE_SA_JSON", raising=False)
    s, slot = _seed(db)
    # Should return silently — no exception even though API isn't configured.
    upsert_event_for_surgery(db, s)
    assert s.google_calendar_sync_status is None   # nothing stamped


def test_upsert_stamps_synced_on_success(db, monkeypatch):
    monkeypatch.setenv("GOOGLE_WORKSPACE_SA_JSON", '{"fake": "json"}')
    s, slot = _seed(db)
    mock_events = MagicMock()
    mock_events.insert.return_value.execute.return_value = {"id": "evt_123"}
    mock_client = MagicMock()
    mock_client.events.return_value = mock_events
    with patch("app.services.google_calendar_sync._build_calendar_client",
                return_value=mock_client):
        upsert_event_for_surgery(db, s)
    assert s.google_calendar_event_id == "evt_123"
    assert s.google_calendar_sync_status == "synced"


def test_upsert_stamps_failed_on_exception(db, monkeypatch):
    monkeypatch.setenv("GOOGLE_WORKSPACE_SA_JSON", '{"fake": "json"}')
    s, slot = _seed(db)
    mock_client = MagicMock()
    mock_client.events.return_value.insert.return_value.execute.side_effect = RuntimeError("boom")
    with patch("app.services.google_calendar_sync._build_calendar_client",
                return_value=mock_client):
        upsert_event_for_surgery(db, s)
    assert s.google_calendar_sync_status == "failed"
    assert "boom" in (s.google_calendar_sync_error or "")
