"""Insurance Contact — billing-team directory of insurance companies.

A small phone-book / claims-link registry: one row per insurance company,
with labeled lists of claims links (URL + label) and phones (number +
label) plus free-form notes.

Permissions:
  - View   : claim:read
  - Edit   : claim:edit  (billing team)
  - Delete : user:manage (admin only)
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, JSON, String, Text

from app.database import Base
from app.models.guid import GUID, new_uuid


class InsuranceContact(Base):
    __tablename__ = "insurance_contacts"

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    company       = Column(String(200), nullable=False, index=True)
    # JSON arrays of {label, url} and {label, number}; empty list if none.
    claims_links  = Column(JSON, nullable=False, default=list)
    phones        = Column(JSON, nullable=False, default=list)
    notes         = Column(Text, nullable=True)
    created_by    = Column(String(120), nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by    = Column(String(120), nullable=True)
    updated_at    = Column(DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)


class InsuranceContactHistory(Base):
    """One row per create/update/delete. Survives parent deletion for audit."""
    __tablename__ = "insurance_contact_history"

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    contact_id   = Column(GUID(), nullable=False, index=True)
    at           = Column(DateTime, default=datetime.utcnow, nullable=False)
    actor        = Column(String(120), nullable=False)
    action       = Column(String(30), nullable=False)
    # values: created | updated | deleted
    before       = Column(JSON, nullable=True)
    after        = Column(JSON, nullable=True)
