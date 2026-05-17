"""Missing Charges — appointments where ModMed says a visit happened but
no charge was created.

Workflow:
  1. Biller uploads an Excel report from ModMed. New rows are added,
     duplicates (same patient_mrn + appointment_date) are skipped.
  2. Biller triages each row: marks Seen / NoShow / Canceled. Seen rows
     become status='needs_to_be_billed'.
  3. Weekly cron emails each provider their needs-to-be-billed list with
     signed-token links. Provider marks each row 'billed' (note complete)
     or 'error' (with explanation).
  4. Biller sees provider-resolved rows, enters the claim number, which
     moves the row to status='billed' (terminal).

A row is fully resolved when its status is billed | no_show | canceled.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid


# Workflow statuses
STATUSES = [
    ("new",                 "New — needs triage"),
    ("needs_to_be_billed",  "Needs to be billed (provider note pending)"),
    ("provider_billed",     "Provider says billed — biller enters claim #"),
    ("provider_error",      "Provider can't bill — see error reason"),
    ("billed",              "Billed — claim # recorded"),
    ("no_show",             "No show"),
    ("canceled",            "Canceled"),
]

TERMINAL_STATUSES = {"billed", "no_show", "canceled"}


class MissingChargeImport(Base):
    __tablename__ = "missing_charge_imports"

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    original_filename = Column(String(255), nullable=False)
    uploaded_by     = Column(String(120), nullable=False)
    uploaded_at     = Column(DateTime, default=datetime.utcnow, nullable=False)
    total_rows      = Column(Integer, default=0)
    new_rows        = Column(Integer, default=0)
    duplicate_rows  = Column(Integer, default=0)
    error_rows      = Column(Integer, default=0)
    notes           = Column(Text, nullable=True)


class MissingCharge(Base):
    __tablename__ = "missing_charges"

    id                  = Column(GUID(), primary_key=True, default=new_uuid)

    # Dedup key: (patient_mrn, appointment_date) — see UniqueConstraint below
    patient_mrn         = Column(String(40),  nullable=False, index=True)
    appointment_date    = Column(Date,        nullable=False, index=True)

    # Snapshot from source row
    patient_name        = Column(String(160), nullable=True)
    patient_dob         = Column(Date,        nullable=True)
    appointment_type    = Column(String(120), nullable=True)
    appointment_status  = Column(String(60),  nullable=True)
    visit_status        = Column(String(60),  nullable=True)
    payer               = Column(String(160), nullable=True)
    primary_provider    = Column(String(120), nullable=True, index=True)
    bill_same_dos       = Column(String(20),  nullable=True)
    bill_same_dos_loc   = Column(String(20),  nullable=True)
    appointment_count   = Column(Integer,     nullable=True)
    patient_link        = Column(Text,        nullable=True)   # ModMed deep link

    # Workflow
    status              = Column(String(40),  default="new", nullable=False, index=True)
    claim_number        = Column(String(80),  nullable=True)
    provider_response_note = Column(Text,     nullable=True)   # explanation when 'provider_error'

    # Provenance
    source_import_id    = Column(GUID(), ForeignKey("missing_charge_imports.id"), nullable=True)

    # Lifecycle
    created_at          = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at          = Column(DateTime, default=datetime.utcnow,
                                 onupdate=datetime.utcnow, nullable=False)
    resolved_at         = Column(DateTime, nullable=True)
    resolved_by         = Column(String(120), nullable=True)

    # Last time we emailed the provider about this row (for weekly cadence)
    last_emailed_at     = Column(DateTime, nullable=True)

    notes_rel = relationship(
        "MissingChargeNote",
        cascade="all, delete-orphan",
        order_by="MissingChargeNote.created_at.desc()",
        backref="charge",
    )

    __table_args__ = (
        # Dedupe at the DB level so even concurrent uploads can't double-add
        # the same row.
        UniqueConstraint("patient_mrn", "appointment_date",
                         name="uq_missing_charge_mrn_date"),
    )


class MissingChargeNote(Base):
    __tablename__ = "missing_charge_notes"

    id          = Column(GUID(), primary_key=True, default=new_uuid)
    charge_id   = Column(GUID(), ForeignKey("missing_charges.id"), nullable=False, index=True)
    author      = Column(String(120), nullable=False)
    body        = Column(Text,        nullable=False)
    created_at  = Column(DateTime,    default=datetime.utcnow, nullable=False)


class ProviderUserMapping(Base):
    """Decisions the biller has made about provider display names that
    appear in ModMed reports ('Last, First').

      • Mapped: user_email is set → weekly cron emails them.
      • Ignored: is_ignored='Y', user_email may be NULL → cron silently
        skips them (used for pseudo-providers like 'Nurse, Schedule' or
        '-' that don't represent real people).
    """
    __tablename__ = "provider_user_mappings"

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    provider_name = Column(String(160), nullable=False, unique=True, index=True)
    user_email    = Column(String(255), nullable=True)
    is_active     = Column(String(1),   default="Y", nullable=False)  # 'Y' | 'N'
    is_ignored    = Column(String(1),   default="N", nullable=False)  # 'Y' = skip, no email
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by    = Column(String(120), nullable=True)
    updated_at    = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow, nullable=False)
