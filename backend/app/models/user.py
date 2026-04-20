"""Minimal User model — email PK + group enum.

Phase 2a0 scope: one group per user, three fixed groups. Custom groups
and per-feature access levels come later (2a00).
"""
from sqlalchemy import Column, String, DateTime, Enum as SAEnum
from datetime import datetime
import enum
from app.database import Base


class UserGroup(str, enum.Enum):
    ADMIN = "admin"
    BILLING = "billing"
    CLINICAL = "clinical"


class User(Base):
    __tablename__ = "users"

    email = Column(String(200), primary_key=True)
    group = Column(SAEnum(UserGroup), default=UserGroup.CLINICAL, nullable=False, index=True)
    display_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
