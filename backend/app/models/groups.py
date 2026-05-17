"""RBAC groups + per-group permission assignments + user memberships.

Permissions themselves live in code (app/services/permissions.py PERMISSIONS).
Groups, group→permission rows, and user→group memberships live here so admins
can edit them at runtime without a deploy.
"""
from __future__ import annotations

from datetime import datetime
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
    Column("added_at", DateTime, default=datetime.utcnow),
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    permissions = relationship(
        "GroupPermission",
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # Reverse side of the M2M defined on User.groups
    members = relationship(
        "User",
        secondary=user_groups,
        back_populates="groups",
        lazy="selectin",
    )


class GroupPermission(Base):
    """One row per (group, permission_string) pair."""
    __tablename__ = "group_permissions"
    __table_args__ = (
        UniqueConstraint("group_id", "permission", name="uq_group_permission"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    group_id = Column(String(36),
                      ForeignKey("groups.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    permission = Column(String(80), nullable=False, index=True)
    granted_at = Column(DateTime, default=datetime.utcnow)
    granted_by = Column(String(200), nullable=True)

    group = relationship("Group", back_populates="permissions")
