"""Google Calendar sync for surgery bookings.

Soft-fail: every public function catches exceptions, stamps the surgery's
google_calendar_sync_status + error, and returns. Never raises.

Configuration (env, optional; if missing, sync is a no-op):
  GOOGLE_WORKSPACE_SA_JSON      JSON service-account credentials
  GOOGLE_CALENDAR_OWNER_EMAIL   the user the SA impersonates to manage events
                                (defaults to acooke@waldorfwomenscare.com)

Required Google scope (granted via domain-wide delegation):
  https://www.googleapis.com/auth/calendar.events
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgerySlot

log = logging.getLogger(__name__)


SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

DEFAULT_OWNER = "acooke@waldorfwomenscare.com"


def _owner_email() -> str:
    return os.environ.get("GOOGLE_CALENDAR_OWNER_EMAIL", DEFAULT_OWNER).strip() or DEFAULT_OWNER


def _is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_WORKSPACE_SA_JSON", "").strip())


def _build_calendar_client():
    """Build a Calendar API service client impersonating the owner email."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        log.warning("google-auth / google-api-python-client not installed; calendar sync disabled")
        return None

    sa_json = os.environ.get("GOOGLE_WORKSPACE_SA_JSON", "").strip()
    if not sa_json:
        return None
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    delegated = creds.with_subject(_owner_email())
    return build("calendar", "v3", credentials=delegated, cache_discovery=False)


# ─── Event shape ────────────────────────────────────────────────────

def _event_body(surgery: Surgery, slot: Optional[SurgerySlot] = None,
                  facility_label: Optional[str] = None) -> dict:
    """Compose the Google Calendar event payload from a Surgery (+ optional
    Slot). When no Slot row exists, fall back to the surgery's own
    scheduled_start_time and estimated_minutes."""
    tz = "America/New_York"
    start_time = (slot.start_time if slot else None) or surgery.scheduled_start_time
    duration   = (slot.duration_minutes if slot else None) or surgery.estimated_minutes or 60
    start_dt = datetime.combine(surgery.scheduled_date, start_time)
    end_dt   = start_dt + timedelta(minutes=duration)

    # Patient-friendly facility label (used inside the title) — fall back to
    # the short label from FACILITY_SHORT when the caller didn't pass one in,
    # so the calendar title doesn't read "medstar" or "crmc".
    facility_summary = facility_label
    if not facility_summary and surgery.selected_facility:
        try:
            from app.services.surgery_klara_drafter import FACILITY_SHORT
            facility_summary = FACILITY_SHORT.get(surgery.selected_facility,
                                                     surgery.selected_facility)
        except Exception:
            facility_summary = surgery.selected_facility

    summary = f"{surgery.patient_name} — "
    if surgery.procedures:
        proc = surgery.procedures[0]
        summary += proc.get("name", "Surgery")
    else:
        summary += "Surgery"
    if facility_summary:
        summary += f" — {facility_summary.strip()}"

    description_lines = [
        f"Patient: {surgery.patient_name}",
        f"Chart #: {surgery.chart_number}",
    ]
    if surgery.procedures:
        description_lines.append(
            "Procedure: " + ", ".join(p.get("name", "?") for p in surgery.procedures))
    if surgery.complexity == "complex":
        description_lines.append("Complexity: COMPLEX")
    if surgery.urgency == "urgent":
        description_lines.append("Urgency: URGENT")
    description_lines.append(f"Duration: {duration} min")

    private = {"surgery_id": str(surgery.id)}
    if slot is not None:
        private["slot_id"] = str(slot.id)
    body = {
        "summary": summary,
        "description": "\n".join(description_lines),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": tz},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": tz},
        "extendedProperties": {"private": private},
    }
    owner = _owner_email().lower()
    if surgery.surgeon_email and surgery.surgeon_email.lower() != owner:
        body["attendees"] = [{"email": surgery.surgeon_email}]
    if facility_label:
        body["location"] = facility_label
    return body


# ─── Public API ─────────────────────────────────────────────────────

def upsert_event_for_surgery(db: Session, surgery: Surgery, facility_label: Optional[str] = None) -> None:
    """Create or update the calendar event for the surgery's current slot.
    Soft-fail: stamps sync_status on success or failure."""
    if not _is_configured():
        return
    if surgery.scheduled_date is None:
        return  # nothing to sync until the surgery has a date
    slot = (db.query(SurgerySlot)
              .filter(SurgerySlot.surgery_id == surgery.id)
              .order_by(SurgerySlot.start_time.asc())
              .first())
    # If no slot exists (e.g., surgery seeded directly without going
    # through the self-schedule path), _event_body falls back to the
    # surgery's own scheduled_start_time + estimated_minutes.
    if slot is None and surgery.scheduled_start_time is None:
        return  # no time anywhere to sync

    try:
        client = _build_calendar_client()
        if client is None:
            return
        body = _event_body(surgery, slot, facility_label=facility_label)
        if surgery.google_calendar_event_id:
            client.events().update(
                calendarId="primary",
                eventId=surgery.google_calendar_event_id,
                body=body,
            ).execute()
        else:
            evt = client.events().insert(calendarId="primary", body=body).execute()
            surgery.google_calendar_event_id = evt.get("id")
        surgery.google_calendar_sync_status = "synced"
        surgery.google_calendar_sync_error  = None
        db.commit()
    except Exception as e:
        surgery.google_calendar_sync_status = "failed"
        surgery.google_calendar_sync_error  = str(e)[:1000]
        db.commit()
        log.warning("calendar upsert failed for surgery %s: %s", surgery.id, e)


def delete_event_for_surgery(db: Session, surgery: Surgery) -> None:
    """Delete the calendar event if one exists. Soft-fail."""
    if not _is_configured():
        return
    event_id = surgery.google_calendar_event_id
    if not event_id:
        return
    try:
        client = _build_calendar_client()
        if client is None:
            return
        client.events().delete(calendarId="primary", eventId=event_id).execute()
        surgery.google_calendar_event_id    = None
        surgery.google_calendar_sync_status = "deleted"
        surgery.google_calendar_sync_error  = None
        db.commit()
    except Exception as e:
        surgery.google_calendar_sync_status = "failed"
        surgery.google_calendar_sync_error  = str(e)[:1000]
        db.commit()
        log.warning("calendar delete failed for surgery %s: %s", surgery.id, e)
