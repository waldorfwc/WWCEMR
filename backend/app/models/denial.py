from sqlalchemy import Column, String, Date, DateTime, Numeric, ForeignKey, Text, Boolean, Enum as SAEnum, Integer
from sqlalchemy.orm import relationship
from datetime import datetime
from app.utils.dt import now_utc_naive
import enum
from app.database import Base
from app.models.guid import GUID, new_uuid


class DenialCategory(str, enum.Enum):
    TIMELY_FILING = "timely_filing"
    AUTHORIZATION = "authorization"
    MEDICAL_NECESSITY = "medical_necessity"
    ELIGIBILITY = "eligibility"
    DUPLICATE = "duplicate"
    CODING = "coding"
    COB = "cob"
    PROVIDER_CREDENTIALING = "provider_credentialing"
    MISSING_INFORMATION = "missing_information"
    BENEFIT_LIMIT = "benefit_limit"
    NON_COVERED = "non_covered"
    OTHER = "other"


class DenialStatus(str, enum.Enum):
    OPEN = "open"
    APPEALING = "appealing"
    OVERTURNED = "overturned"
    UPHELD = "upheld"
    WRITTEN_OFF = "written_off"
    RESUBMITTED = "resubmitted"


class Denial(Base):
    __tablename__ = "denials"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    claim_id = Column(GUID(), ForeignKey("claims.id"), index=True)
    service_line_id = Column(GUID(), ForeignKey("service_lines.id"), nullable=True)

    carc_code = Column(String(20))
    rarc_code = Column(String(20), nullable=True)
    group_code = Column(String(5))
    carc_description = Column(String(500), nullable=True)
    rarc_description = Column(String(500), nullable=True)
    category = Column(SAEnum(DenialCategory), default=DenialCategory.OTHER)

    denied_amount = Column(Numeric(12, 2), default=0)
    denial_date = Column(Date, nullable=True)

    status = Column(SAEnum(DenialStatus), default=DenialStatus.OPEN)

    appeal_deadline = Column(Date, nullable=True)
    appeal_submitted_date = Column(Date, nullable=True)
    appeal_decision_date = Column(Date, nullable=True)
    appeal_decision = Column(String(100), nullable=True)
    appeal_level = Column(Integer, default=1)

    recommended_action = Column(String(100), nullable=True)
    write_off_recommended = Column(Boolean, default=False)
    write_off_reason = Column(Text, nullable=True)
    appealable = Column(Boolean, default=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_utc_naive)
    updated_at = Column(DateTime, default=now_utc_naive, onupdate=now_utc_naive)

    claim = relationship("Claim", back_populates="denials")
    service_line = relationship("ServiceLine")
    appeals = relationship("Appeal", back_populates="denial", cascade="all, delete-orphan")
