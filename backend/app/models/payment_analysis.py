from sqlalchemy import Column, String, Date, Numeric, Text
from app.database import Base
from app.models.guid import GUID, new_uuid


class PaymentAnalysis(Base):
    __tablename__ = "payment_analysis"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(20), nullable=False, index=True)
    posting_date = Column(Date, index=True)
    payment_source = Column(String(50))  # Insurance Payment, Patient Payment
    payment_method = Column(String(50))  # Check, EFT, Credit Card
    payment_amount = Column(Numeric(12, 2))
    service_date = Column(Date)
    description = Column(Text)
    provider = Column(String(100))
    credit_category = Column(String(100))
    allocation_amount = Column(Numeric(12, 2))
    raw_line = Column(Text)
