"""Tier tables for the per-module permission model.

Spec: docs/superpowers/specs/2026-06-06-permissions-redesign-design.md
Plan: docs/superpowers/plans/2026-06-06-permissions-redesign.md (Task 1)
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.database import Base


class GroupModuleTier(Base):
    """Per-group tier grant for a single module.
    Composes with other groups' grants via max() in the resolver."""
    __tablename__ = "group_module_tiers"

    group_id = Column(
        String(36),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True, nullable=False,
    )
    module = Column(String(60), primary_key=True, nullable=False)
    tier   = Column(Integer, nullable=False)


class UserModuleOverride(Base):
    """Per-user tier override for a single module.
    Always wins over group grants. tier=0 means 'denied'."""
    __tablename__ = "user_module_overrides"

    user_email = Column(
        String(200),
        ForeignKey("users.email", ondelete="CASCADE"),
        primary_key=True, nullable=False,
    )
    module    = Column(String(60), primary_key=True, nullable=False)
    tier      = Column(Integer, nullable=False)
    added_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    added_by  = Column(String(120), nullable=False)
