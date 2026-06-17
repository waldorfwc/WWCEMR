"""Pellet scheduling (Phase 3): availability templates (recurrence rule per
location) and the materialized fixed-length bookable slots."""
from __future__ import annotations

from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Index,
                        Integer, JSON, String, Time, UniqueConstraint)

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.utils.dt import now_utc_naive


class PelletAvailabilityTemplate(Base):
    __tablename__ = "pellet_availability_templates"
    __table_args__ = (Index("ix_pellet_avail_location", "location"),)

    id = Column(GUID(), primary_key=True, default=new_uuid)
    location = Column(String(40), nullable=False)
    recurrence_kind = Column(String(20), nullable=False)  # daily|weekly|weekly_nth|monthly_day|specific_dates
    weekday = Column(Integer, nullable=True)        # 0=Mon..6=Sun
    nth_in_month = Column(JSON, nullable=True)       # [1,3]
    day_of_month = Column(Integer, nullable=True)    # 1..31
    specific_dates = Column(JSON, nullable=True)     # ["2026-07-01", ...]
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    slot_minutes = Column(Integer, nullable=True)    # null → config default
    provider = Column(String(120), nullable=True)
    effective_from = Column(Date, nullable=True)
    effective_through = Column(Date, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    created_by = Column(String(200), nullable=True)


class PelletSlot(Base):
    __tablename__ = "pellet_slots"
    __table_args__ = (
        Index("ix_pellet_slot_loc_date", "location", "slot_date"),
        UniqueConstraint("location", "slot_date", "start_time",
                         name="uq_pellet_slot_loc_date_time"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    version_id = Column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version_id}
    template_id = Column(GUID(),
                         ForeignKey("pellet_availability_templates.id", ondelete="SET NULL"),
                         nullable=True)
    location = Column(String(40), nullable=False)
    provider = Column(String(120), nullable=True)
    slot_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    status = Column(String(20), default="open", nullable=False)  # open|booked|blocked|canceled
    pellet_visit_id = Column(GUID(), ForeignKey("pellet_visits.id", ondelete="SET NULL"),
                             nullable=True)
    is_addon = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    created_by = Column(String(200), nullable=True)
