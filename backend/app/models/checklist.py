"""Checklist / task-reminder system models.

Phase A: foundational schema for daily task templates + per-user instances.
Notification preferences ride on the user record (added via lightweight migration).
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Column, String, Date, DateTime, Boolean, ForeignKey, JSON, Table, Text, Time, Integer,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.guid import GUID, new_uuid


# Many-to-many: task templates can target multiple groups
task_template_groups = Table(
    "task_template_groups",
    Base.metadata,
    Column("template_id", GUID(),
           ForeignKey("task_templates.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", String(36),
           ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)


class TaskTemplate(Base):
    """A reusable task definition. Per-day instances are spawned from these
    by the recurrence engine.

    Targeting — assignees are the union of:
      - users in any of `assigned_groups`
      - users listed in `assigned_users` (JSON list of emails)
    The legacy `assigned_permission` column is retained for back-compat
    but no longer evaluated (see services/checklist_service.py). The
    legacy `role` field is still here but no longer authoritative; it's
    used as a display hint and during the one-time migration to seed
    assigned_groups from the matching practice-role group.
    """
    __tablename__ = "task_templates"

    id = Column(GUID(), primary_key=True, default=new_uuid)

    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    instructions = Column(Text, nullable=True)         # how-to / context shown when expanded

    # Legacy single-role target — kept until Phase 5 cleanup.
    role = Column(String(40), nullable=True, index=True)

    # New multi-source targeting (any combination — generator unions them)
    assigned_users = Column(JSON, nullable=True)             # ["sarah@…", "oliver@…"]
    assigned_permission = Column(String(80), nullable=True)  # e.g. "payment:post"

    category = Column(String(40), nullable=False)
    # values: clinical, admin, billing, safety, compliance, communication

    # Legacy frequency triple — kept for back-compat. New code reads
    # recurrence_kind / recurrence_weekdays / recurrence_days_of_month /
    # anchor_date / interval_n. Backfill writes both for one release.
    frequency = Column(String(20), nullable=False, default="daily")
    weekday = Column(Integer, nullable=True)          # 0=Mon..6=Sun for weekly
    day_of_month = Column(Integer, nullable=True)     # 1..28 for monthly

    # New flexible recurrence (Phase 5)
    # values: daily | weekdays_of_week | days_of_month | anniversary
    #       | every_n_days | every_n_months | every_n_years | on_demand
    recurrence_kind = Column(String(30), nullable=True)
    recurrence_weekdays = Column(JSON, nullable=True)        # [0,2,4] = Mon/Wed/Fri
    recurrence_days_of_month = Column(JSON, nullable=True)   # [1, 15] = 1st & 15th
    anchor_date = Column(Date, nullable=True)                # for anniversary + every_n_*
    interval_n = Column(Integer, nullable=True)              # for every_n_*

    # Weekend rule when computed due-date lands on Sat/Sun.
    # values: skip | roll_to_monday   (default roll_to_monday for monthly+,
    # skip for daily — applied in the generator, this column overrides)
    weekend_rule = Column(String(20), nullable=True)

    due_time = Column(Time, nullable=True)            # e.g. 18:00 for EOD tasks
    priority = Column(String(10), default="medium", nullable=False)  # low/medium/high/critical

    active = Column(Boolean, default=True, nullable=False)

    # Yes/No question framing (Phase 5).
    # If set, replaces `title` as the prompt the user sees.
    question_text = Column(Text, nullable=True)
    # values: none | count | reason
    followup_kind = Column(String(20), default="none", nullable=False)
    followup_prompt = Column(Text, nullable=True)

    # Manager who is notified when this task isn't completed in time, or
    # when the user answers No. Single email so we have one accountable
    # person per task.
    escalate_to_email = Column(String(120), nullable=True)
    # Hours past due before manager is notified (default 24h)
    escalate_after_hours = Column(Integer, default=24, nullable=False)

    # Training prerequisite (Phase 6).
    # When True, the assignee filter only includes users with an active
    # TrainingCertification on this template. If False, anyone in the
    # targeted groups/users gets the task without a training gate.
    requires_training = Column(Boolean, default=True, nullable=False)
    # Optional link to the in-depth training material (Google Doc, video,
    # SOP). Shown to trainers/trainees in the certification flow.
    training_material_url = Column(Text, nullable=True)
    # Expiration policy.
    # values: never | years | months | weeks | days | specific_date
    expires_kind = Column(String(20), default="never", nullable=False)
    # For relative expiration (years/months/weeks/days): how many of those.
    expires_value = Column(Integer, nullable=True)
    # For absolute expiration: hard date everyone's cert lapses on (e.g.
    # company-wide HIPAA reset on Dec 31).
    expires_on_date = Column(Date, nullable=True)

    # Notification overrides — when null, fall back to user prefs
    notify_morning = Column(Boolean, default=True, nullable=False)
    notify_afternoon = Column(Boolean, default=False, nullable=False)
    notify_overdue = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(120), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    updated_by = Column(String(120), nullable=True)

    instances = relationship("TaskInstance", back_populates="template",
                             cascade="all, delete-orphan")
    assigned_groups = relationship(
        "Group",
        secondary=task_template_groups,
        lazy="selectin",
    )


class TaskInstance(Base):
    """A specific assignment of a template to a user on a specific day."""
    __tablename__ = "task_instances"
    __table_args__ = (
        Index("ix_task_inst_user_due", "assigned_to_email", "due_date"),
        Index("ix_task_inst_status", "status"),
        UniqueConstraint("template_id", "assigned_to_email", "due_date",
                         name="uq_task_inst_template_user_date"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    template_id = Column(GUID(), ForeignKey("task_templates.id", ondelete="CASCADE"),
                         nullable=False)

    assigned_to_email = Column(String(120), nullable=False, index=True)
    due_date = Column(Date, nullable=False, index=True)
    due_at = Column(DateTime, nullable=True)               # exact deadline if time-of-day

    status = Column(String(20), default="pending", nullable=False)
    # pending / in_progress / done / skipped / overdue

    # Yes/No answer (Phase 5). When `answer="no"`, the follow-up fields
    # capture how-many or why.
    answer = Column(String(10), nullable=True)             # "yes" | "no"
    followup_count = Column(Integer, nullable=True)        # for followup_kind=count
    followup_text = Column(Text, nullable=True)            # for followup_kind=reason

    completed_at = Column(DateTime, nullable=True)
    completed_by = Column(String(120), nullable=True)
    skipped_reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    # Manager-escalation tracking — set by the notification job once an
    # escalation has been sent so we don't spam the manager.
    escalation_sent_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    template = relationship("TaskTemplate", back_populates="instances")


class PainPoint(Base):
    """Free-text pain point a user can log at the end (or start) of their
    checklist. Visible to the user's manager on the manager dashboard."""
    __tablename__ = "pain_points"
    __table_args__ = (
        Index("ix_painpoint_user_date", "user_email", "occurred_on"),
        Index("ix_painpoint_status", "status"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    user_email = Column(String(120), nullable=False, index=True)
    occurred_on = Column(Date, nullable=False, index=True)  # the checklist day it was logged for
    body = Column(Text, nullable=False)

    # Lifecycle:
    #   new          — just submitted; owner hasn't responded
    #   in_progress  — owner has responded; submitter hasn't acknowledged
    #   completed    — submitter acknowledged the response
    status = Column(String(20), default="new", nullable=False)
    # Owner's response (single comment for now — keeps the schema simple)
    reviewed_by = Column(String(120), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    response = Column(Text, nullable=True)
    # Submitter's acknowledgement of the response
    acknowledged_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
