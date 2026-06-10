from sqlalchemy import Column, String, Date, DateTime, Integer, Index
from datetime import datetime
from app.utils.dt import now_utc_naive
from app.database import Base
from app.models.guid import GUID, new_uuid


class PatientDocument(Base):
    __tablename__ = "patient_documents"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    chart_number = Column(String(20), nullable=False, index=True)
    doc_type = Column(String(120), nullable=False, index=True)
    doc_date = Column(Date, nullable=True, index=True)
    doc_id = Column(String(20), nullable=True)          # PrimeSuite internal document ID
    page_number = Column(Integer, default=1)
    filename = Column(String(300), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size_kb = Column(Integer, default=0)
    indexed_at = Column(DateTime, default=now_utc_naive)

    __table_args__ = (
        Index("ix_doc_chart_type", "chart_number", "doc_type"),
        Index("ix_doc_chart_date", "chart_number", "doc_date"),
    )
