from sqlalchemy import Column, String, Date, DateTime, Numeric, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
from app.utils.dt import now_utc_naive
import enum
from app.database import Base
from app.models.guid import GUID, new_uuid


class PaymentType(str, enum.Enum):
    INSURANCE_PAYMENT = "insurance_payment"
    PATIENT_PAYMENT = "patient_payment"
    COPAY = "copay"
    DEDUCTIBLE = "deductible"
    COINSURANCE = "coinsurance"
    WRITE_OFF = "write_off"
    REFUND = "refund"
    REVERSAL = "reversal"
    ADJUSTMENT = "adjustment"


class Payment(Base):
    __tablename__ = "payments"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(GUID(), ForeignKey("patients.id"), nullable=True, index=True)
    claim_id = Column(GUID(), ForeignKey("claims.id"), nullable=True, index=True)

    payment_type = Column(SAEnum(PaymentType))
    amount = Column(Numeric(12, 2), default=0)
    payment_date = Column(Date)
    date_of_service = Column(Date, nullable=True)

    payer_name = Column(String(200), nullable=True)
    check_number = Column(String(100), nullable=True)
    era_file_id = Column(GUID(), ForeignKey("era_files.id"), nullable=True)

    payment_method = Column(String(50), nullable=True)
    receipt_number = Column(String(100), nullable=True)

    posted_by = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=now_utc_naive)
    updated_at = Column(DateTime, default=now_utc_naive, onupdate=now_utc_naive)

    patient = relationship("Patient")
    claim = relationship("Claim")
