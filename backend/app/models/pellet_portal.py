"""Pellet patient-portal support models: insertion consent (1-yr validity),
the staff patient-action feed, login-challenge throttling, and patient file
uploads. Mirrors the surgery portal's SurgeryActivity + auth-attempt patterns
but keyed off PelletPatient. (Pellet Patient Portal — Phase 1.)"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.utils.dt import now_utc_naive


class PelletConsent(Base):
    __tablename__ = "pellet_consents"
    __table_args__ = (Index("ix_pellet_consent_patient", "pellet_patient_id"),)

    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    boldsign_envelope_id = Column(String(120), nullable=True)
    template_id = Column(String(120), nullable=True)
    status = Column(String(20), nullable=False, default="sent")  # sent|signed|declined|expired
    signed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)   # signed_at + 365d
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)

    @property
    def is_valid(self) -> bool:
        return (self.status == "signed" and self.expires_at is not None
                and self.expires_at > now_utc_naive())


class PelletActivity(Base):
    __tablename__ = "pellet_activity"
    __table_args__ = (
        Index("ix_pellet_activity_patient", "pellet_patient_id"),
        Index("ix_pellet_activity_created", "created_at"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    # mammo_uploaded | labs_self_reported | consent_signed | consent_sent | payment_made | booked
    kind = Column(String(40), nullable=False)
    summary = Column(String(300), nullable=False)
    actor = Column(String(20), nullable=False, default="patient")  # patient | system
    detail = Column(Text, nullable=True)            # optional JSON string
    created_at = Column(DateTime, default=now_utc_naive, nullable=False, index=True)
    handled_at = Column(DateTime, nullable=True)    # staff verified/cleared
    handled_by = Column(String(200), nullable=True)
    read_at = Column(DateTime, nullable=True)
    read_by = Column(String(200), nullable=True)


class PelletPortalAuthAttempt(Base):
    __tablename__ = "pellet_portal_auth_attempts"
    __table_args__ = (Index("ix_pellet_authattempt_token", "challenge_token"),)

    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False)
    challenge_token = Column(String(80), nullable=False)
    code_hash = Column(String(120), nullable=False)
    purpose = Column(String(20), nullable=False, default="login")
    attempts = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    consumed_at = Column(DateTime, nullable=True)


class PelletPortalUpload(Base):
    """A file the PATIENT uploaded through the portal (the mammogram image/PDF).
    The clinical PelletPatientMammo/Lab tables are staff-entry with NOT-NULL
    fields and no file column, so patient uploads land here for staff review."""
    __tablename__ = "pellet_portal_uploads"
    __table_args__ = (Index("ix_pellet_upload_patient", "pellet_patient_id"),)

    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    kind = Column(String(20), nullable=False, default="mammo")   # mammo
    filename = Column(String(255), nullable=True)
    storage_path = Column(Text, nullable=False)
    content_type = Column(String(100), nullable=True)
    uploaded_at = Column(DateTime, default=now_utc_naive, nullable=False)
