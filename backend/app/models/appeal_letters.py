"""Appeal letter generation — payer addresses + drafted/sent letters."""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive
from sqlalchemy import (
    Column, String, Date, DateTime, Numeric, Integer, ForeignKey, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.guid import GUID, new_uuid


class PayerAddress(Base):
    """Mailing/fax addresses for insurance companies' appeals departments.
    Seeded with top WWC payers; extensible per practice."""
    __tablename__ = "payer_addresses"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    payer_name = Column(String(200), nullable=False, index=True)   # match against ActiveClaim.insurance_company (ilike)
    payer_id = Column(String(50), nullable=True)                   # X12 payer ID when known

    appeals_dept_name = Column(String(200), nullable=True)
    address_line_1 = Column(String(200))
    address_line_2 = Column(String(200), nullable=True)
    city = Column(String(100))
    state = Column(String(2))
    zip_code = Column(String(15))

    appeals_fax = Column(String(20), nullable=True)
    appeals_phone = Column(String(20), nullable=True)
    appeals_email = Column(String(200), nullable=True)

    notes = Column(Text, nullable=True)         # e.g. "First-level appeals only"
    updated_at = Column(DateTime, default=now_utc_naive, onupdate=now_utc_naive)
    created_at = Column(DateTime, default=now_utc_naive)


class AppealLetter(Base):
    """A drafted or sent appeal letter for a specific active claim."""
    __tablename__ = "appeal_letters"
    __table_args__ = (
        Index("ix_appeal_letter_claim", "active_claim_id"),
        Index("ix_appeal_letter_status", "status"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    active_claim_id = Column(GUID(), ForeignKey("active_claims.id", ondelete="CASCADE"),
                             nullable=False)

    # Content
    template_type = Column(String(40), nullable=False)
    # values: medical_necessity, timely_filing, cob, unbundling,
    #         missing_info, benefits, coding, general
    level = Column(Integer, nullable=False, default=1)   # 1, 2, or 3 (IRO)

    subject = Column(String(300))
    body = Column(Text)                 # editable letter body
    additional_verbiage = Column(Text, nullable=True)   # WWC's custom standard language

    # Recipient (from PayerAddress at draft time, but editable per letter)
    recipient_name = Column(String(200))
    recipient_address = Column(Text)    # full multi-line address as it should appear
    recipient_fax = Column(String(20), nullable=True)

    # Signer (defaults to practice manager, configurable)
    signer_name = Column(String(200))
    signer_credentials = Column(String(50), nullable=True)
    signer_title = Column(String(100))

    # Status & lifecycle
    status = Column(String(20), default="draft", nullable=False)
    # draft / generated / sent / responded / approved / denied / withdrawn

    pdf_path = Column(String(500), nullable=True)
    sent_via = Column(String(20), nullable=True)        # fax / mail / portal / download
    sent_at = Column(DateTime, nullable=True)
    sent_to = Column(String(200), nullable=True)        # fax# or mailing address summary
    fax_log_id = Column(GUID(), nullable=True)          # fk-style ref to fax_log if faxed

    response_received_at = Column(DateTime, nullable=True)
    response_outcome = Column(String(40), nullable=True)   # approved / denied / partial / pending
    response_notes = Column(Text, nullable=True)

    # Metadata
    generated_by = Column(String(200))                # user email
    used_ai_drafting = Column(Integer, default=0)     # 0/1 (sqlite-friendly bool)
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    updated_at = Column(DateTime, default=now_utc_naive, onupdate=now_utc_naive, nullable=False)

    claim = relationship("ActiveClaim", backref="appeal_letters")
