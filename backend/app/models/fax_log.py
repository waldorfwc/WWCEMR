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

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_fax_chart_sent", "chart_number", "sent_at"),
        Index("ix_fax_status_checked", "status", "last_checked_at"),
    )
