"""Patient transactional SMS — templates + per-send audit.

Mirrors patient_email.py but simpler:
- SMS has no subject (just body)
- SMS has no HTML; plain text only
- Sends are gated on Surgery.sms_consent — patient must opt in first

Statuses on PatientSms:
  sent     — Twilio API returned success
  failed   — Twilio API returned an error (failure_reason populated)
  skipped  — Template missing, recipient missing, OR no consent
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, JSON, String, Text,
    UniqueConstraint,
)

from app.database import Base
from app.models.guid import GUID, new_uuid


SMS_TEMPLATE_KINDS = (
    "sms_payment_link",
    "sms_surgery_confirmation",
    "sms_surgery_reminder",
    "sms_generic_message",
    "sms_portal_login_code",
)


PATIENT_SMS_STATUSES = ("sent", "failed", "skipped")


class SmsTemplate(Base):
    __tablename__ = "sms_templates"
    __table_args__ = (
        UniqueConstraint("kind", name="uq_sms_template_kind"),
    )

    id          = Column(GUID(), primary_key=True, default=new_uuid)
    kind        = Column(String(60), nullable=False)
    label       = Column(String(120), nullable=False)
    body        = Column(Text, nullable=False)
    # SMS body — plain text, {{var}} substitution, no HTML.
    is_active   = Column(Boolean, default=True, nullable=False)
    notes       = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow, nullable=False)
    updated_by  = Column(String(120), nullable=True)


class PatientSms(Base):
    __tablename__ = "patient_sms"
    __table_args__ = (
        Index("ix_patient_sms_surgery", "surgery_id"),
        Index("ix_patient_sms_kind",    "template_kind"),
        Index("ix_patient_sms_sent_at", "sent_at"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id      = Column(GUID(),
                              ForeignKey("surgeries.id", ondelete="SET NULL"),
                              nullable=True)
    chart_number    = Column(String(20), nullable=True)
    to_phone        = Column(String(40), nullable=False)
    template_kind   = Column(String(60), nullable=True)
    rendered_body   = Column(Text, nullable=False)
    segments        = Column(String(10), nullable=True)
    # Twilio counts billable segments per message (≤160 chars = 1 segment;
    # >160 chars splits into more). We store the count for cost tracking.
    status          = Column(String(20), nullable=False, default="sent")
    failure_reason  = Column(Text, nullable=True)
    sent_at         = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_by         = Column(String(120), nullable=True)
    context         = Column(JSON, nullable=True)
    twilio_sid      = Column(String(80), nullable=True)
    # Twilio's message SID for tracing in Twilio dashboard.
