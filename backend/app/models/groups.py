"""RBAC groups + user memberships.

Per-module tier grants for a group live in `group_module_tiers`
(see app/models/module_tier.py). The legacy `group_permissions` table
was dropped in Phase 4 of the permissions redesign.
"""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive
import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, String, Table, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────
# users ↔ groups (many-to-many)

user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_email", String(200),
           ForeignKey("users.email", ondelete="CASCADE"), primary_key=True),
    Column("group_id", String(36),
           ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    Column("added_at", DateTime, default=now_utc_naive),
    Column("added_by", String(200), nullable=True),
)


class Group(Base):
    __tablename__ = "groups"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(80), nullable=False, unique=True)
    description = Column(String(400), nullable=True)
    # Cannot be deleted in the UI when True (the seeded groups).
    # Members and permissions can still be edited.
    system_protected = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=now_utc_naive)
    updated_at = Column(DateTime, default=now_utc_naive, onupdate=now_utc_naive)

    # Reverse side of the M2M defined on User.groups
    members = relationship(
        "User",
        secondary=user_groups,
        back_populates="groups",
        lazy="selectin",
    )
