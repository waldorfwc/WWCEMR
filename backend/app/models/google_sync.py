"""Google Workspace sync — exclusion list + run audit.

GoogleSyncExclusion: emails the sync should skip even when present in
Google Workspace. Used for service accounts, shared mailboxes, and any
Google account that shouldn't have a system user.

GoogleSyncRun: a row per sync invocation, capturing what changed for
display on the admin page (last successful run, counts, errors).
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, Integer, JSON
from app.database import Base
from app.models.guid import GUID, new_uuid


class GoogleSyncExclusion(Base):
    """An email that the sync must NEVER auto-provision or auto-suspend."""
    __tablename__ = "google_sync_exclusions"

    email = Column(String(200), primary_key=True)
    reason = Column(Text, nullable=True)
    added_by = Column(String(120), nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class GoogleSyncRun(Base):
    """A row per sync run — kept for the admin page status display.
    Not pruned automatically; cheap rows."""
    __tablename__ = "google_sync_runs"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)

    triggered_by = Column(String(120), nullable=True)  # email or "system:cron"
    status = Column(String(20), default="running", nullable=False)
    # values: running | success | error

    # Counters
    google_users_seen = Column(Integer, default=0, nullable=False)
    created = Column(Integer, default=0, nullable=False)
    activated = Column(Integer, default=0, nullable=False)
    suspended = Column(Integer, default=0, nullable=False)
    excluded = Column(Integer, default=0, nullable=False)

    error_message = Column(Text, nullable=True)
    detail_json = Column(JSON, nullable=True)         # arbitrary debug payload
