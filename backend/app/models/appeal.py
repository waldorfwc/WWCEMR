from sqlalchemy import Column, String, Date, DateTime, ForeignKey, Text, Boolean, Enum as SAEnum, Integer
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base
from app.models.guid import GUID, new_uuid


class AppealStatus(str, enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    SUBMITTED = "submitted"
    PENDING_DECISION = "pending_decision"
    APPROVED = "approved"
    DENIED = "denied"


class Appeal(Base):
    __tablename__ = "appeals"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    denial_id = Column(GUID(), ForeignKey("denials.id"), index=True)

    level = Column(Integer, default=1)
    status = Column(SAEnum(AppealStatus), default=AppealStatus.DRAFT)

    letter_subject = Column(String(500), nullable=True)
    letter_body = Column(Text, nullable=True)
    letter_file_path = Column(String(1000), nullable=True)
    supporting_docs = Column(Text, nullable=True)

    deadline = Column(Date, nullable=True)
    submitted_date = Column(Date, nullable=True)
    decision_date = Column(Date, nullable=True)
    decision_notes = Column(Text, nullable=True)

    generated_by_ai = Column(Boolean, default=False)
    ai_model = Column(String(100), nullable=True)

    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    denial = relationship("Denial", back_populates="appeals")
