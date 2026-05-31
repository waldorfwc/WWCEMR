"""Patient transactional email — templates + per-send audit.

  EmailTemplate    — one row per template kind. Admin can edit subject +
                     html_body via the admin tab (Phase I8). `kind` is the
                     stable key callers reference; new templates are
                     created via the seed (Phase I2) or admin UI.

  PatientEmail     — one row per email actually delivered to a patient.
                     Append-only; lets us answer "did we send X to this
                     patient on this surgery" and underpins reminder
                     idempotency.

Statuses on PatientEmail:
  sent     — SMTP returned OK
  failed   — SMTP returned an error (failure_reason populated)
  skipped  — Template missing or recipient address blank
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, JSON, String, Text,
    UniqueConstraint,
)

from app.database import Base
from app.models.guid import GUID, new_uuid


# Stable kinds the system uses today. Admin UI shows whatever rows exist;
# callers reference these constants for safety.
EMAIL_TEMPLATE_KINDS = (
    "stripe_payment_link",
    "stripe_payment_receipt",
    "surgery_confirmation",
    "surgery_reminder",
    "docusign_consent_sent",
    "generic_patient_message",
    "surgery_post_op_followup",
)


PATIENT_EMAIL_STATUSES = ("sent", "failed", "skipped")


class EmailTemplate(Base):
    __tablename__ = "email_templates"
    __table_args__ = (
        UniqueConstraint("kind", name="uq_email_template_kind"),
    )

    id          = Column(GUID(), primary_key=True, default=new_uuid)
    kind        = Column(String(60), nullable=False)
    label       = Column(String(120), nullable=False)
    # Human-readable name shown in admin UI (e.g. "Surgery confirmation").
    subject     = Column(Text, nullable=False)
    html_body   = Column(Text, nullable=False)
    text_body   = Column(Text, nullable=True)
    # If text_body is null, the SMTP send falls back to a stripped-tags
    # version of html_body.
    is_active   = Column(Boolean, default=True, nullable=False)
    notes       = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow,
                         onupdate=datetime.utcnow, nullable=False)
    updated_by  = Column(String(120), nullable=True)


class PatientEmail(Base):
    __tablename__ = "patient_emails"
    __table_args__ = (
        Index("ix_patient_email_surgery",  "surgery_id"),
        Index("ix_patient_email_kind",     "template_kind"),
        Index("ix_patient_email_sent_at",  "sent_at"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id      = Column(GUID(),
                             ForeignKey("surgeries.id", ondelete="SET NULL"),
                             nullable=True)
    # Nullable so future non-surgery patient emails (pellet membership,
    # generic messaging unconnected to a Surgery row) can use the same
    # audit table without invented FKs.
    chart_number    = Column(String(20), nullable=True)
    to_email        = Column(String(200), nullable=False)
    template_kind   = Column(String(60), nullable=True)
    # Null for ad-hoc free-text composer sends.
    rendered_subject = Column(Text, nullable=False)
    rendered_html    = Column(Text, nullable=False)
    status           = Column(String(20), nullable=False, default="sent")
    failure_reason   = Column(Text, nullable=True)
    sent_at          = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_by          = Column(String(120), nullable=True)
    # Email of staff member who triggered, or 'system:cron' for reminders,
    # 'system:webhook' for Stripe receipts, etc.
    context          = Column(JSON, nullable=True)
    # Free-form payload of vars used in rendering, for debugging.
