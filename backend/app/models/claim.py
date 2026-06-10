from sqlalchemy import Column, String, Date, DateTime, Numeric, Integer, ForeignKey, Text, JSON, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.utils.dt import now_utc_naive
import enum
from app.database import Base
from app.models.guid import GUID, new_uuid
from app.models.mixins import SoftDeleteMixin


class ClaimStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    PARTIAL = "partial"
    DENIED = "denied"
    ADJUSTED = "adjusted"
    REVERSED = "reversed"
    APPEALED = "appealed"
    WRITTEN_OFF = "written_off"


class InsuranceOrder(str, enum.Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    PATIENT = "patient"


class Claim(Base, SoftDeleteMixin):
    __tablename__ = "claims"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(GUID(), ForeignKey("patients.id"), nullable=True, index=True)

    claim_number = Column(String(100), index=True)
    payer_claim_number = Column(String(100), nullable=True)
    patient_control_number = Column(String(100), nullable=True)

    date_of_service_from = Column(Date, nullable=True)
    date_of_service_to = Column(Date, nullable=True)
    statement_date = Column(Date, nullable=True)
    received_date = Column(Date, nullable=True)

    payer_name = Column(String(200), nullable=True)
    payer_id = Column(String(50), nullable=True)
    insurance_order = Column(SAEnum(InsuranceOrder), default=InsuranceOrder.PRIMARY)
    subscriber_id = Column(String(100), nullable=True)
    group_number = Column(String(100), nullable=True)

    rendering_provider_npi = Column(String(20), nullable=True)
    rendering_provider_name = Column(String(200), nullable=True)
    billing_provider_npi = Column(String(20), nullable=True)

    billed_amount = Column(Numeric(12, 2), default=0)
    allowed_amount = Column(Numeric(12, 2), default=0)
    paid_amount = Column(Numeric(12, 2), default=0)
    patient_responsibility = Column(Numeric(12, 2), default=0)
    contractual_adjustment = Column(Numeric(12, 2), default=0)
    other_adjustment = Column(Numeric(12, 2), default=0)
    balance = Column(Numeric(12, 2), default=0)

    status = Column(SAEnum(ClaimStatus), default=ClaimStatus.PENDING)
    claim_filing_indicator = Column(String(10), nullable=True)

    era_file_id = Column(GUID(), ForeignKey("era_files.id"), nullable=True)
    check_number = Column(String(100), nullable=True)
    check_date = Column(Date, nullable=True)
    check_amount = Column(Numeric(12, 2), nullable=True)

    raw_clp_segment = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    # Phase 2d enrichment (from Claims Analysis)
    follow_up_date = Column(Date, nullable=True)
    follow_up_reason = Column(String(200), nullable=True)
    last_submission_date = Column(Date, nullable=True)
    claim_state = Column(String(20), nullable=True)   # "Open" | "Closed"

    created_at = Column(DateTime, default=now_utc_naive)
    updated_at = Column(DateTime, default=now_utc_naive, onupdate=now_utc_naive)

    patient = relationship("Patient", back_populates="claims")
    service_lines = relationship("ServiceLine", back_populates="claim", cascade="all, delete-orphan")
    adjustments = relationship("ClaimAdjustment", back_populates="claim", cascade="all, delete-orphan")
    denials = relationship("Denial", back_populates="claim", cascade="all, delete-orphan")
    era_file = relationship("EraFile", back_populates="claims")


class ServiceLine(Base):
    __tablename__ = "service_lines"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    claim_id = Column(GUID(), ForeignKey("claims.id"), index=True)

    procedure_code = Column(String(20), nullable=True)
    modifier_1 = Column(String(10), nullable=True)
    modifier_2 = Column(String(10), nullable=True)
    modifier_3 = Column(String(10), nullable=True)
    modifier_4 = Column(String(10), nullable=True)
    revenue_code = Column(String(10), nullable=True)
    units = Column(Numeric(8, 2), default=1)
    description = Column(String(500), nullable=True)

    date_of_service_from = Column(Date, nullable=True)
    date_of_service_to = Column(Date, nullable=True)

    billed_amount = Column(Numeric(12, 2), default=0)
    allowed_amount = Column(Numeric(12, 2), default=0)
    paid_amount = Column(Numeric(12, 2), default=0)
    patient_responsibility = Column(Numeric(12, 2), default=0)
    contractual_adjustment = Column(Numeric(12, 2), default=0)
    other_adjustment = Column(Numeric(12, 2), default=0)

    diagnosis_codes = Column(JSON, default=list)
    created_at = Column(DateTime, default=now_utc_naive)

    claim = relationship("Claim", back_populates="service_lines")
    adjustments = relationship("ServiceLineAdjustment", back_populates="service_line", cascade="all, delete-orphan")


class ClaimAdjustment(Base):
    __tablename__ = "claim_adjustments"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    claim_id = Column(GUID(), ForeignKey("claims.id"), index=True)

    group_code = Column(String(5))
    reason_code = Column(String(20))
    amount = Column(Numeric(12, 2), default=0)
    quantity = Column(Numeric(8, 2), nullable=True)
    reason_description = Column(String(500), nullable=True)

    claim = relationship("Claim", back_populates="adjustments")


class ServiceLineAdjustment(Base):
    __tablename__ = "service_line_adjustments"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    service_line_id = Column(GUID(), ForeignKey("service_lines.id"), index=True)

    group_code = Column(String(5))
    reason_code = Column(String(20))
    amount = Column(Numeric(12, 2), default=0)
    quantity = Column(Numeric(8, 2), nullable=True)
    reason_description = Column(String(500), nullable=True)

    service_line = relationship("ServiceLine", back_populates="adjustments")


class EraFile(Base):
    __tablename__ = "era_files"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    filename = Column(String(500))
    file_path = Column(String(1000))
    payer_name = Column(String(200), nullable=True)
    payer_id = Column(String(50), nullable=True)
    check_number = Column(String(100), nullable=True)
    check_date = Column(Date, nullable=True)
    check_amount = Column(Numeric(12, 2), nullable=True)
    transaction_count = Column(Integer, default=0)
    status = Column(String(50), default="processed")
    error_log = Column(Text, nullable=True)
    imported_at = Column(DateTime, default=now_utc_naive)
    imported_by = Column(String(100), nullable=True)

    claims = relationship("Claim", back_populates="era_file")
