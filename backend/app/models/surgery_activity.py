"""SurgeryActivity — persisted feed of patient + system events.

Each row is a point-in-time event (slot claimed, consent signed/declined,
document uploaded, labs self-reported, paid, date-change requested) plus
system events (auto-unresponsive, step-overdue). The surgery scheduler's
"Recent Activity" surface reads these newest-first with unread/read state
and a nav badge.

This is distinct from SurgerySchedulerNotice (an email idempotency
ledger) and SurgeryNotification (the outbound Klara/calendar log) — this
table is the in-app notification feed the coordinator reads.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.utils.dt import now_utc_naive


class SurgeryActivity(Base):
    __tablename__ = "surgery_activity"
    __table_args__ = (
        Index("ix_surgery_activity_surgery", "surgery_id"),
        Index("ix_surgery_activity_created", "created_at"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(), ForeignKey("surgeries.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    # date_picked | rescheduled | cancelled | consent_signed |
    # consent_declined | document_uploaded | labs_reported | payment_made |
    # date_change_requested | auto_unresponsive | step_overdue
    kind = Column(String(40), nullable=False)
    summary = Column(String(300), nullable=False)   # human one-liner
    actor = Column(String(20), nullable=False, default="patient")  # patient | system
    created_at = Column(DateTime, default=now_utc_naive, nullable=False,
                        index=True)
    read_at = Column(DateTime, nullable=True)
    read_by = Column(String(200), nullable=True)
