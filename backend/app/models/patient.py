from sqlalchemy import Column, String, Date, Text, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.guid import GUID, new_uuid


class Patient(Base):
    __tablename__ = "patients"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_id = Column(String(50), unique=True, index=True)
    first_name = Column(String(100))
    last_name = Column(String(100))
    date_of_birth = Column(Date, nullable=True)
    address = Column(Text, nullable=True)
    phone = Column(String(20), nullable=True)
    email = Column(String(120), nullable=True)

    primary_insurance_name = Column(String(200), nullable=True)
    primary_insurance_id = Column(String(100), nullable=True)
    primary_group_number = Column(String(100), nullable=True)
    secondary_insurance_name = Column(String(200), nullable=True)
    secondary_insurance_id = Column(String(100), nullable=True)
    secondary_group_number = Column(String(100), nullable=True)
    tertiary_insurance_name = Column(String(200), nullable=True)
    tertiary_insurance_id = Column(String(100), nullable=True)
    tertiary_group_number = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    claims = relationship("Claim", back_populates="patient")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"
