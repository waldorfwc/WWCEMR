"""Audit log for PrimeSuite report imports.

Tracks every Charge / Claims / Transaction Detail file ingested, captures
its row-level fingerprints, and supports a *drift check* on re-import:
when the same period is imported twice, the system compares the two
pulls and flags any discrepancies (changed rows, missing rows, new rows
in supposedly-closed periods).
"""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive

from sqlalchemy import (
    Column, String, Date, DateTime, Integer, Numeric, Text, ForeignKey, Index,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid


class ImportAuditLog(Base):
    """One row per file imported.

    Multiple imports for the same (report_type, period_start, period_end)
    are allowed — the latest one is the current truth, prior ones stay
    for audit history.
    """
    __tablename__ = "import_audit_log"

    id = Column(GUID(), primary_key=True, default=new_uuid)

    report_type = Column(String(40), nullable=False, index=True)  # 'transaction_detail' | 'claims_analysis' | 'charge_analysis'
    period_start = Column(Date, nullable=True, index=True)
    period_end = Column(Date, nullable=True, index=True)

    source_filename = Column(String(255), nullable=False)
    file_sha256 = Column(String(64), nullable=False)         # the literal file's hash

    row_count = Column(Integer, nullable=False, default=0)
    # Header-level sanity totals (sum of key money columns from the source)
    total_amount = Column(Numeric(14, 2), nullable=True)
    secondary_total = Column(Numeric(14, 2), nullable=True)  # report-specific second total

    # Drift report vs the prior import for this same (report_type, period)
    drift_report_json = Column(Text, nullable=True)
    rows_added = Column(Integer, nullable=True)
    rows_removed = Column(Integer, nullable=True)
    rows_changed = Column(Integer, nullable=True)

    imported_by = Column(String(255), nullable=True)
    imported_at = Column(DateTime, nullable=False, default=now_utc_naive)

    fingerprints = relationship(
        "ImportRowFingerprint",
        back_populates="audit_log",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "ix_import_audit_period",
            "report_type", "period_start", "period_end",
        ),
    )


class ImportRowFingerprint(Base):
    """Per-row fingerprint for drift detection.

    natural_key  — concatenation of fields that uniquely identify a row in
                   the source report (e.g. patient_id|visit_id|posting_date
                   |procedure|type for Transaction Detail).
    value_hash   — short sha256 of the value columns we care about. Same
                   natural_key with different value_hash => row was edited.
    """
    __tablename__ = "import_row_fingerprint"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    audit_log_id = Column(
        GUID(),
        ForeignKey("import_audit_log.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    natural_key = Column(String(400), nullable=False)
    value_hash = Column(String(64), nullable=False)

    audit_log = relationship("ImportAuditLog", back_populates="fingerprints")

    __table_args__ = (
        Index("ix_fingerprint_audit_key", "audit_log_id", "natural_key"),
    )
