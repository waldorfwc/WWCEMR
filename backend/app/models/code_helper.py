"""ORM models for the Code Helper feature.

See docs/superpowers/specs/2026-05-19-code-helper-design.md for the data
model rationale + per-CPT JSON shape.
"""
from datetime import datetime
from app.utils.dt import now_utc_naive
from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, JSON,
    String, Text,
)

from app.database import Base
from app.models.guid import GUID, new_uuid


class CodeHelperRequest(Base):
    """One row per AI code-generation call. AI output is kept verbatim
    so the audit log is reproducible."""
    __tablename__ = "code_helper_requests"
    __table_args__ = (
        Index("ix_code_helper_req_requested_at", "requested_at"),
        Index("ix_code_helper_req_patient",      "patient_id"),
        Index("ix_code_helper_req_requested_by", "requested_by"),
    )

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    requested_at  = Column(DateTime, default=now_utc_naive, nullable=False)
    requested_by  = Column(String(120), nullable=False)

    # Input — exactly one of (note_text, source_pdf_storage_filename) is set
    note_text                   = Column(Text, nullable=True)
    source_pdf_storage_filename = Column(String(255), nullable=True)
    payer_name                  = Column(String(120), nullable=True)

    # Patient (AI-extracted, user-editable; FK set when roster match is unambiguous)
    patient_name = Column(String(160), nullable=True)
    patient_dob  = Column(Date,          nullable=True)
    patient_id   = Column(String(20), ForeignKey("patients.patient_id"), nullable=True)

    # AI output, verbatim
    cpt_codes    = Column(JSON, default=list, nullable=False)
    icd10_codes  = Column(JSON, default=list, nullable=False)

    # Audit
    ai_model         = Column(String(60),  nullable=False)
    ai_input_tokens  = Column(Integer,     nullable=True)
    ai_output_tokens = Column(Integer,     nullable=True)
    error            = Column(Text,        nullable=True)


class CodeHelperDenial(Base):
    """Practice's persistent list of CPT/ICD codes that get denied by
    specific payers (or universally when payer_name is null)."""
    __tablename__ = "code_helper_denials"
    __table_args__ = (
        Index("ix_code_helper_denials_lookup",
              "code", "payer_name", "is_active"),
    )

    id          = Column(GUID(), primary_key=True, default=new_uuid)
    code        = Column(String(20), nullable=False)
    code_type   = Column(String(10), nullable=False)  # 'cpt' or 'icd10'
    payer_name  = Column(String(120), nullable=True)  # null = all payers
    reason      = Column(Text, nullable=True)
    is_active   = Column(Boolean, default=True, nullable=False)
    added_by    = Column(String(120), nullable=False)
    added_at    = Column(DateTime, default=now_utc_naive, nullable=False)
    updated_at  = Column(DateTime, default=now_utc_naive,
                          onupdate=now_utc_naive, nullable=False)
