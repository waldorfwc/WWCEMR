from sqlalchemy import Column, String, Date, DateTime, Integer, Float, Index
from datetime import datetime
from app.utils.dt import now_utc_naive
from app.database import Base
from app.models.guid import GUID, new_uuid


class PatientDirectory(Base):
    """
    Authoritative chart_number -> name/DOB mapping extracted from Phreesia
    Demographic PDFs. Used to match intake documents (which only have name+DOB)
    back to chart numbers.
    """
    __tablename__ = "patient_directory"

    chart_number = Column(String(20), primary_key=True)
    patient_name = Column(String(200), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    middle_name = Column(String(100), nullable=True)
    dob = Column(Date, nullable=True, index=True)
    gender = Column(String(20), nullable=True)
    address = Column(String(300), nullable=True)
    phone = Column(String(30), nullable=True)
    email = Column(String(200), nullable=True)
    source_file = Column(String(300), nullable=True)
    last_updated = Column(DateTime, default=now_utc_naive)

    __table_args__ = (
        Index("ix_pd_lastname_dob", "last_name", "dob"),
    )


class IntakeDocument(Base):
    """
    Documents from the intake zip archive — organized by name + DOB,
    then matched to chart numbers via the patient directory.
    """
    __tablename__ = "intake_documents"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    patient_name_raw = Column(String(200), nullable=False)
    dob = Column(Date, nullable=False, index=True)
    doc_category = Column(String(120), nullable=True)   # "ID&Insurance", "Practice Agreements", etc.
    doc_year = Column(Integer, nullable=True)            # 2024, 2025, 2026
    filename = Column(String(400), nullable=False)
    file_path = Column(String(600), nullable=False)
    file_size_kb = Column(Integer, default=0)
    file_type = Column(String(20), nullable=True)        # pdf, jpg, etc.

    # Matching
    matched_chart_number = Column(String(20), nullable=True, index=True)
    match_confidence = Column(String(20), nullable=True)  # "exact", "fuzzy", "unmatched"
    match_score = Column(Float, default=0.0)

    indexed_at = Column(DateTime, default=now_utc_naive)

    __table_args__ = (
        Index("ix_intake_name_dob", "patient_name_raw", "dob"),
        Index("ix_intake_chart", "matched_chart_number"),
    )
