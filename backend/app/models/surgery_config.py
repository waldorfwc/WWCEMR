"""Surgery module configuration tables (Phase B).

Four small tables back the admin UI on /surgery/rules:

  surgery_config              — key/value store for thresholds
                                (office_full_threshold, office_lookahead_days,
                                hospital_lookahead_days, etc.)
  surgery_alert_recipients    — per-alert email lists
                                (office_release, hospital_release)
  facilities                  — replaces hardcoded FACILITY_LABEL dicts
                                across the codebase
  surgery_procedure_templates — default durations + CPT for each procedure
                                kind, used by the coordinator override flow
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint,
)

from app.database import Base
from app.models.guid import GUID, new_uuid


class SurgeryConfig(Base):
    __tablename__ = "surgery_config"

    key        = Column(String(60), primary_key=True)
    value      = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow, nullable=False)
    updated_by = Column(String(120), nullable=True)


class SurgeryAlertRecipient(Base):
    __tablename__ = "surgery_alert_recipients"
    __table_args__ = (
        UniqueConstraint("alert_kind", "email", name="uq_alert_recip_kind_email"),
    )

    id         = Column(GUID(), primary_key=True, default=new_uuid)
    alert_kind = Column(String(40), nullable=False)
    # values: office_release | hospital_release
    email      = Column(String(200), nullable=False)
    added_by   = Column(String(120), nullable=True)
    added_at   = Column(DateTime, default=datetime.utcnow, nullable=False)


class Facility(Base):
    __tablename__ = "facilities"
    __table_args__ = (
        UniqueConstraint("code", name="uq_facility_code"),
    )

    id         = Column(GUID(), primary_key=True, default=new_uuid)
    code       = Column(String(20), nullable=False)
    label      = Column(String(120), nullable=False)
    address    = Column(Text, nullable=True)
    is_active  = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=100, nullable=False)
    created_by = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by = Column(String(120), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow, nullable=False)


class SurgeryProcedureTemplate(Base):
    __tablename__ = "surgery_procedure_templates"
    __table_args__ = (
        UniqueConstraint("code", name="uq_proc_template_code"),
    )

    id                       = Column(GUID(), primary_key=True, default=new_uuid)
    code                     = Column(String(40), nullable=False)
    name                     = Column(String(200), nullable=False)
    procedure_kind           = Column(String(20), nullable=False)
    # values: minor | major | office | robotic_180 | robotic_240
    default_duration_minutes = Column(Integer, nullable=False)
    default_cpt_code         = Column(String(20), nullable=True)
    is_active                = Column(Boolean, default=True, nullable=False)
    created_by               = Column(String(120), nullable=True)
    created_at               = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by               = Column(String(120), nullable=True)
    updated_at               = Column(DateTime, default=datetime.utcnow,
                                          onupdate=datetime.utcnow, nullable=False)
