"""Recall workflow — patients to call back for follow-up / annual exam.

Three tables:
  recall_entries       — one row per patient currently on the recall list
  recall_suppressions  — chart numbers permanently excluded (DNC / declined / etc.)
  recall_call_logs     — every call attempt + view event for audit

DOB is intentionally NOT stored here — joined from patient_directory at
read time so we have one source of truth.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer,
    JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid


class WWEVisit(Base):
    """Historical Well-Woman Exam visits.

    Sourced from Greenway PrimeSuite billing exports (2014-2019 + 2020-present)
    and ModMed reports going forward. Used by the recall detail to show the
    patient's full preventive-visit history, total visit count, and to
    compute expected next visit (latest + 13 months).

    Unique on (chart_number, visit_date, procedure_code) so re-imports
    don't duplicate.
    """
    __tablename__ = "wwe_visits"
    __table_args__ = (
        UniqueConstraint("chart_number", "visit_date", "procedure_code",
                          name="uq_wwe_chart_date_code"),
        Index("ix_wwe_chart", "chart_number"),
        Index("ix_wwe_date", "visit_date"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    chart_number = Column(String(20), nullable=False)
    visit_date = Column(Date, nullable=False)
    # For Greenway billing rows: the CPT (99384–99397). For ModMed
    # appointment rows: the appointment type (WWE-EST / WWE-NEW). Used as
    # part of the dedupe key, so it's never NULL.
    procedure_code = Column(String(20), nullable=True)
    # values: greenway | modmed | manual
    source = Column(String(20), nullable=False, default="greenway")
    # Status of the appointment/visit:
    #   completed  — past visit on the books (Greenway "billed" or ModMed "Checked Out")
    #   scheduled  — future appointment in ModMed
    #   cancelled  — cancelled in ModMed
    #   noshow     — no-show in ModMed
    # Greenway rows are always "completed" (you only bill for visits you saw).
    status = Column(String(20), nullable=False, default="completed")
    is_future = Column(Boolean, nullable=False, default=False)
    # When this row was last touched by an import — useful for staleness
    # debugging and for "most recent report wins" tie-breaks.
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    imported_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RecallEntry(Base):
    """A patient currently on the recall list."""
    __tablename__ = "recall_entries"
    __table_args__ = (
        Index("ix_recall_chart", "chart_number"),
        Index("ix_recall_status", "status"),
        Index("ix_recall_due", "recall_due"),
        UniqueConstraint("chart_number", "recall_type",
                          name="uq_recall_chart_type"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    chart_number = Column(String(20), nullable=False, index=True)

    # Patient snapshot — cached for fast list rendering.
    patient_name = Column(String(200), nullable=True)
    dob = Column(Date, nullable=True)
    cell_phone = Column(String(30), nullable=True)
    primary_phone = Column(String(30), nullable=True)
    email = Column(String(200), nullable=True)
    primary_insurance = Column(String(200), nullable=True)
    primary_plan = Column(String(200), nullable=True)

    # Recall metadata
    recall_type = Column(String(80), nullable=True)        # WWE / Colpo / Post-op / etc.
    recall_status = Column(String(20), nullable=True)      # source-of-truth from Smartsheet
    priority = Column(Integer, nullable=True)              # 1=high, 2=med, 3=low
    last_visit = Column(Date, nullable=True)
    recall_due = Column(Date, nullable=True)
    recall_create = Column(Date, nullable=True)
    recall_expiration = Column(Date, nullable=True)

    # Internal status — drives whether this row appears in the active queue
    status = Column(String(20), default="active", nullable=False)
    # active / paused / completed / suppressed

    # Attempt rollup — denormalized cache of latest call info for fast display
    attempts = Column(Integer, default=0, nullable=False)
    last_outcome = Column(String(80), nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    last_worked_by = Column(String(200), nullable=True)
    latest_comment = Column(Text, nullable=True)

    # Cooldown — hide from queue until this datetime
    cooldown_until = Column(DateTime, nullable=True)

    # Soft claim — prevents two callers from working the same patient at once.
    # Set when a user opens the detail drawer or hits dial; auto-expires
    # CLAIM_TTL minutes later. Cleared on outcome submit.
    claimed_by = Column(String(200), nullable=True)
    claimed_until = Column(DateTime, nullable=True)

    # Audit
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)
    source = Column(String(40), nullable=True)              # 'smartsheet' / 'manual' / 'auto'
    smartsheet_row_id = Column(String(40), nullable=True, index=True)

    call_logs = relationship("RecallCallLog", back_populates="entry",
                             cascade="all, delete-orphan")


class RecallSuppression(Base):
    """Chart numbers permanently excluded from recall lists.

    Once suppressed, the patient cannot be re-added. Future imports of
    appointment reports or the Smartsheet won't bring them back; the
    importer checks this table first.
    """
    __tablename__ = "recall_suppressions"

    chart_number = Column(String(20), primary_key=True)
    reason = Column(String(40), nullable=False)
    # do_not_call / declined / deceased / left_practice / unsubscribed / other

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(200), nullable=True)


class RecallCallLog(Base):
    """Every call attempt + view event for audit. HIPAA-friendly: log who
    saw what and when, not just who edited."""
    __tablename__ = "recall_call_logs"
    __table_args__ = (
        Index("ix_recall_call_chart", "chart_number"),
        Index("ix_recall_call_at", "occurred_at"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    recall_entry_id = Column(GUID(),
                             ForeignKey("recall_entries.id", ondelete="CASCADE"),
                             nullable=True)
    chart_number = Column(String(20), nullable=False, index=True)

    event_type = Column(String(40), nullable=False)
    # 'detail_viewed' / 'call_attempted' / 'outcome_logged' / 'note_added'

    user_email = Column(String(200), nullable=True)
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    outcome = Column(String(80), nullable=True)
    notes = Column(Text, nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    entry = relationship("RecallEntry", back_populates="call_logs")


class RecallFilterPreset(Base):
    """A named filter preset on the recall dashboard. Stored per user so
    each staffer keeps their own working set (e.g. 'Salley's overdue
    patients', 'Past-due 90+ days from BCBS')."""
    __tablename__ = "recall_filter_presets"
    __table_args__ = (
        Index("ix_recall_filter_owner", "owner_email"),
        UniqueConstraint("owner_email", "name", name="uq_recall_filter_owner_name"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    owner_email  = Column(String(200), nullable=False)
    name         = Column(String(120), nullable=False)
    filters_json = Column(JSON, nullable=False, default=dict)
    is_default   = Column(Boolean, default=False, nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at   = Column(DateTime, default=datetime.utcnow,
                          onupdate=datetime.utcnow, nullable=False)
