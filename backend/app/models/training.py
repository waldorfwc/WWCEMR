"""Training & certification models.

Three-actor flow:
  1. A manager (training:authorize) authorizes someone as a TRAINER for a
     specific TaskTemplate by inserting a TrainerAuthorization row.
  2. The TRAINER certifies a TRAINEE by inserting a TrainingCertification
     with status='pending_trainee', capturing trainer signature + time.
  3. The TRAINEE acknowledges they were trained — flips status to 'active'
     (or 'disputed' if they reject the assertion). Signature + time captured.

After the cert is active, the assignee filter in the checklist generator
includes that user for that template. `expires_on` (computed at sign time
from the template's expires_kind/value/specific_date) bounds how long the
cert is valid; expired certs are treated like none.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Column, String, DateTime, Date, Text, Integer, Boolean, ForeignKey, Index,
    UniqueConstraint,
)
from app.database import Base
from app.models.guid import GUID, new_uuid


class TrainerAuthorization(Base):
    """Manager-issued grant: this user is allowed to certify others on
    this template. One row per (user, template). Soft-revoked via
    revoked_at — never hard-deleted so the audit trail survives.
    """
    __tablename__ = "trainer_authorizations"
    __table_args__ = (
        UniqueConstraint("user_email", "template_id",
                          name="uq_trainer_user_template"),
        Index("ix_trainer_template", "template_id"),
        Index("ix_trainer_user", "user_email"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    user_email = Column(String(120), nullable=False)
    template_id = Column(GUID(), ForeignKey("task_templates.id", ondelete="CASCADE"),
                         nullable=False)

    authorized_by = Column(String(120), nullable=False)   # the manager
    authorized_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String(120), nullable=True)
    revoked_reason = Column(Text, nullable=True)

    notes = Column(Text, nullable=True)


class TrainingCertification(Base):
    """A trainee's certification on a single TaskTemplate.

    status transitions:
      pending_trainee  → trainer has signed, waiting on trainee
      active           → trainee confirmed; assignment-eligible (if not expired)
      disputed         → trainee rejected the trainer's assertion
      revoked          → admin/manager pulled the cert
    """
    __tablename__ = "training_certifications"
    __table_args__ = (
        UniqueConstraint("user_email", "template_id",
                          name="uq_cert_user_template"),
        Index("ix_cert_status", "status"),
        Index("ix_cert_user", "user_email"),
        Index("ix_cert_template", "template_id"),
        Index("ix_cert_expires", "expires_on"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    user_email = Column(String(120), nullable=False)        # trainee
    template_id = Column(GUID(), ForeignKey("task_templates.id", ondelete="CASCADE"),
                         nullable=False)

    trainer_email = Column(String(120), nullable=False)
    trainer_signed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    trainee_signed_at = Column(DateTime, nullable=True)

    status = Column(String(20), default="pending_trainee", nullable=False)
    expires_on = Column(Date, nullable=True)               # computed at trainee-sign

    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String(120), nullable=True)
    revoked_reason = Column(Text, nullable=True)

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
