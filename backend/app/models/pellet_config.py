"""Pellet module config table — KV store backing /pellets/config.
Mirrors LarcConfig."""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive

from sqlalchemy import Column, DateTime, JSON, String

from app.database import Base


class PelletConfig(Base):
    __tablename__ = "pellet_config"

    key        = Column(String(60), primary_key=True)
    value      = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=now_utc_naive,
                            onupdate=now_utc_naive, nullable=False)
    updated_by = Column(String(120), nullable=True)
