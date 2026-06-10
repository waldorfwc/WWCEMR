"""Patient portal auth — SMS-code challenges issued during sign-in."""
from __future__ import annotations
from datetime import datetime
from app.utils.dt import now_utc_naive

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, String,
)

from app.database import Base
from app.models.guid import GUID, new_uuid


class PatientPortalAuthCode(Base):
    __tablename__ = "patient_portal_auth_codes"
    __table_args__ = (
        Index("ix_patient_portal_auth_codes_surgery", "surgery_id"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id      = Column(GUID(),
                              ForeignKey("surgeries.id", ondelete="CASCADE"),
                              nullable=False)
    challenge_token = Column(String(64), nullable=False, unique=True)
    code_hash       = Column(String(60), nullable=False)
    # bcrypt hash of the 6-digit code we SMS'd. Plaintext code never persisted.
    fail_count      = Column(Integer, default=0, nullable=False)
    expires_at      = Column(DateTime, nullable=False)
    used_at         = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=now_utc_naive, nullable=False)
    sent_to_phone   = Column(String(40), nullable=True)
    # For audit only. The phone is already on the Surgery row.
    purpose         = Column(String(20), nullable=True)
    # values: login | payment | review
    # verify_code requires the caller to pass the same purpose the
    # challenge was issued for — without this, a login code could be
    # replayed to authorize a payment step-up. (Fable portal audit C1.)
