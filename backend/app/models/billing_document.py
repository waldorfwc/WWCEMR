"""Insurance Documents — scanned paper EOBs, patient payment slips,
insurance letters, etc. Anyone can upload; documents can be assigned to
one or more workers (which restricts visibility) or left unassigned
(everyone sees them). Every access is audited.

Files live on disk under BILLING_DOCS_STORAGE_PATH (defaults to the
external drive at /Volumes/OWC External/Insurance Docs). The DB row only
stores the on-disk filename and metadata.
"""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive
from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, JSON, String, Text,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.models.mixins import SoftDeleteMixin


# Allowed classification values. Editable here (UI dropdown is driven
# by /billing/documents/picklists).
CLASSIFICATIONS = [
    ("paper_eob",        "Paper EOB"),
    ("greenway_eob",     "Greenway EOB"),
    ("patient_payment",  "Patient Payment"),
    ("insurance_letter", "Insurance Letter"),
    ("denial",           "Denial"),
    ("other",            "Other"),
]

STATUSES = [
    ("new",         "New"),
    ("in_progress", "In Progress"),
    ("worked",      "Worked"),
]


class BillingDocument(Base, SoftDeleteMixin):
    __tablename__ = "billing_documents"

    id = Column(GUID(), primary_key=True, default=new_uuid)

    # On-disk metadata
    original_filename = Column(String(255), nullable=False)
    storage_filename  = Column(String(255), nullable=False, unique=True)
    file_size_bytes   = Column(Integer, nullable=True)
    page_count        = Column(Integer, nullable=True)
    mime_type         = Column(String(80), default="application/pdf")
    # SHA-256 of the uploaded bytes. Used to detect duplicate uploads.
    content_hash      = Column(String(64), nullable=True, index=True)

    # Workflow
    classification    = Column(String(40), default="other", nullable=False)
    status            = Column(String(20), default="new",   nullable=False)

    uploaded_by       = Column(String(120), nullable=False)
    uploaded_at       = Column(DateTime, default=now_utc_naive, nullable=False)

    # JSON list of user emails. Empty list / null = unassigned (everyone sees).
    assigned_to       = Column(JSON, default=list, nullable=False)

    worked_by         = Column(String(120), nullable=True)
    worked_at         = Column(DateTime, nullable=True)

    notes_rel = relationship(
        "BillingDocumentNote",
        cascade="all, delete-orphan",
        order_by="BillingDocumentNote.created_at.desc()",
        backref="document",
    )
    access_log = relationship(
        "BillingDocumentAccess",
        cascade="all, delete-orphan",
        order_by="BillingDocumentAccess.at.desc()",
        backref="document",
    )


class BillingDocumentNote(Base):
    __tablename__ = "billing_document_notes"

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    document_id  = Column(GUID(), ForeignKey("billing_documents.id"), nullable=False, index=True)
    author       = Column(String(120), nullable=False)
    body         = Column(Text,        nullable=False)
    created_at   = Column(DateTime,    default=now_utc_naive, nullable=False)


class BillingDocumentAccess(Base):
    """Audit row — one per access/edit event. Heavy table by design; gives
    a complete who-touched-what trail."""
    __tablename__ = "billing_document_access"

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    document_id  = Column(GUID(), ForeignKey("billing_documents.id"), nullable=False, index=True)
    actor        = Column(String(120), nullable=False)
    action       = Column(String(40),  nullable=False)
    # Examples: viewed | downloaded | classified | assigned | unassigned
    #           | worked | reopened | note_added
    at           = Column(DateTime, default=now_utc_naive, nullable=False, index=True)
    detail       = Column(JSON, nullable=True)
