"""FaxLog model — one row per fax attempt. Persisted for audit, retry, status polling."""
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, JSON, Enum as SAEnum, Index
from datetime import datetime
import enum
from app.database import Base
from app.models.guid import GUID, new_uuid


class FaxLogStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


class GroupingMode(str, enum.Enum):
    SEPARATE = "separate"
    COMBINED = "combined"
    BY_TYPE = "by_type"


class FaxLog(Base):
    __tablename__ = "fax_logs"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    chart_number = Column(String(20), nullable=False, index=True)
    doc_ids = Column(JSON, nullable=False)  # list of document UUID strings
    grouping_mode = Column(SAEnum(GroupingMode), default=GroupingMode.SEPARATE, nullable=False)
    dest_fax = Column(String(40), nullable=False)

    ringcentral_message_id = Column(String(64), nullable=True, index=True)
    status = Column(SAEnum(FaxLogStatus), default=FaxLogStatus.QUEUED, nullable=False, index=True)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_checked_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    sent_by = Column(String(200), nullable=True)

    retry_of = Column(GUID(), ForeignKey("fax_logs.id"), nullable=True)

    # Client-supplied idempotency key. A double-clicked Send button (or
    # retried HTTP call) sends the same client_request_id and we return
    # the existing row instead of re-faxing. Optional; absent means the
    # call falls back to ordinary (non-idempotent) send. (Fable C3.)
    client_request_id = Column(String(80), nullable=True)

    # Cover-page text persisted at send so a fax_retry can resend with
    # the same recipient instructions instead of a context-free document.
    # (Fable recalls audit H6.)
    cover_text = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_fax_chart_sent", "chart_number", "sent_at"),
        Index("ix_fax_status_checked", "status", "last_checked_at"),
        # Idempotency: at most one fax_log per (chart, client_request_id)
        # when client supplies an id. Partial index is added by the
        # lightweight-migration on Postgres only.
    )
