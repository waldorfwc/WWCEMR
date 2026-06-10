"""Patient<->staff messages per surgery + reusable message templates."""
from datetime import datetime
from app.utils.dt import now_utc_naive

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text

from app.database import Base
from app.models.guid import GUID, new_uuid


class SurgeryMessage(Base):
    __tablename__ = "surgery_messages"
    __table_args__ = (
        Index("ix_surgery_messages_thread", "surgery_id", "sent_at"),
    )

    id                  = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id          = Column(GUID(),
                                    ForeignKey("surgeries.id", ondelete="CASCADE"),
                                    nullable=False)
    author_kind         = Column(String(20), nullable=False)
    author_email        = Column(String(200), nullable=True)
    body                = Column(Text, nullable=False)
    sent_at             = Column(DateTime, default=now_utc_naive,
                                    nullable=False)
    read_by_patient_at  = Column(DateTime, nullable=True)
    read_by_staff_at    = Column(DateTime, nullable=True)


class MessageTemplate(Base):
    __tablename__ = "message_templates"

    id          = Column(GUID(), primary_key=True, default=new_uuid)
    name        = Column(String(120), nullable=False)
    body        = Column(Text, nullable=False)
    created_at  = Column(DateTime, default=now_utc_naive, nullable=False)
    updated_at  = Column(DateTime, default=now_utc_naive,
                            onupdate=now_utc_naive, nullable=False)
