"""Personal task list — lightweight to-do items employees create for
themselves, optionally with subtasks, assignees, and shared viewers.

This is separate from the checklist TaskTemplate / TaskInstance flow,
which is recurring-template-driven. Personal tasks are one-off, free-form
items owned by the creator.

Hierarchy: one level. A row is either a top-level task (parent_id is
NULL) or a subtask (parent_id points at the parent task). No grandchildren.

Sharing:
- owner_email — created the task; full control (edit/delete/share/close)
- assignee_email — single primary owner of the work; can update status
- shared_with — JSON list of emails; view-only access
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Date, DateTime, JSON, Integer, ForeignKey, Index,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid


PRIORITIES = ("high", "medium", "low")
STATUSES   = ("new", "in_progress", "closed")


class PersonalTask(Base):
    __tablename__ = "personal_tasks"
    __table_args__ = (
        Index("ix_personal_task_owner", "owner_email"),
        Index("ix_personal_task_assignee", "assignee_email"),
        Index("ix_personal_task_parent", "parent_id"),
        Index("ix_personal_task_status", "status"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    # Optimistic locking — same patches don't trample each other.
    version_id = Column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version_id}

    parent_id = Column(GUID(), ForeignKey("personal_tasks.id"), nullable=True)

    owner_email    = Column(String(120), nullable=False)
    # JSON list of emails — primary doers (multi-assignee)
    assignees      = Column(JSON, default=list, nullable=False)
    # JSON list of emails — view-only sharing
    shared_with    = Column(JSON, default=list, nullable=False)
    # Legacy single-assignee column. Kept so old data isn't lost; new code
    # reads/writes `assignees` instead. Safe to drop later.
    assignee_email = Column(String(120), nullable=True)

    title       = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    priority    = Column(String(10), default="medium", nullable=False)  # high|medium|low
    status      = Column(String(20), default="new",    nullable=False)  # new|in_progress|closed
    due_date    = Column(Date, nullable=True)

    # Display ordering within a parent (drag-to-reorder later if needed).
    position    = Column(Integer, default=0, nullable=False)

    # Lifecycle
    closed_at = Column(DateTime, nullable=True)
    closed_by = Column(String(120), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(120), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                          onupdate=datetime.utcnow, nullable=False)
    updated_by = Column(String(120), nullable=True)

    # Subtasks are queried explicitly (no SQLAlchemy relationship needed):
    #   db.query(PersonalTask).filter(PersonalTask.parent_id == self.id)
