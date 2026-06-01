"""Reputation module — per-employee review pipeline."""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text,
)

from app.database import Base
from app.models.guid import GUID, new_uuid


class ReputationProfile(Base):
    __tablename__ = "reputation_profiles"

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    user_email    = Column(String(200), nullable=True)
    display_name  = Column(String(120), nullable=False)
    role_label    = Column(String(80), nullable=True)
    location      = Column(String(40), nullable=True)
    # "white_plains" | "arlington" | "brandywine" — drives which Google
    # review URL gets shown to 5-star reviewers.
    qr_token      = Column(String(40), nullable=False, unique=True, index=True)
    active        = Column(Boolean, default=True, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = Column(DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)


class ReputationScan(Base):
    __tablename__ = "reputation_scans"
    __table_args__ = (
        Index("ix_reputation_scans_profile", "profile_id", "scanned_at"),
    )

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    profile_id      = Column(GUID(),
                                ForeignKey("reputation_profiles.id",
                                            ondelete="CASCADE"),
                                nullable=False)
    scanned_at      = Column(DateTime, default=datetime.utcnow,
                                nullable=False)
    ip_address      = Column(String(45), nullable=True)
    user_agent      = Column(String(300), nullable=True)
    points_credited = Column(Integer, default=0, nullable=False)


class ReputationReview(Base):
    __tablename__ = "reputation_reviews"
    __table_args__ = (
        Index("ix_reputation_reviews_profile", "profile_id", "submitted_at"),
    )

    id                   = Column(GUID(), primary_key=True, default=new_uuid)
    profile_id           = Column(GUID(),
                                      ForeignKey("reputation_profiles.id",
                                                  ondelete="CASCADE"),
                                      nullable=False)
    scan_id              = Column(GUID(),
                                      ForeignKey("reputation_scans.id",
                                                  ondelete="SET NULL"),
                                      nullable=True)
    stars                = Column(Integer, nullable=False)
    body                 = Column(Text, nullable=True)
    patient_first_name   = Column(String(80), nullable=True)
    patient_last_initial = Column(String(2), nullable=True)
    patient_chart_number = Column(String(20), nullable=True)
    patient_phone        = Column(String(20), nullable=True)
    consent_to_display   = Column(Boolean, default=False, nullable=False)
    approved_for_embed   = Column(Boolean, default=False, nullable=False)
    google_clicked_at    = Column(DateTime, nullable=True)
    submitted_at         = Column(DateTime, default=datetime.utcnow,
                                      nullable=False)


class ReputationPhoneChallenge(Base):
    """Short-lived (5-min) SMS-code challenge used by the patient-verify
    flow on the review form. Separate from PatientPortalAuthCode because
    that table requires a surgery_id FK; here the challenge has no
    surgery context until verify-check looks up the matching Surgery."""
    __tablename__ = "reputation_phone_challenges"

    id              = Column(GUID(), primary_key=True, default=new_uuid)
    challenge_token = Column(String(64), nullable=False, unique=True, index=True)
    code_hash       = Column(String(120), nullable=False)
    phone           = Column(String(20), nullable=False)
    expires_at      = Column(DateTime, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
