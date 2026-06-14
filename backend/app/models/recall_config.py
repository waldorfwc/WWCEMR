"""Recall module config table — KV store backing /recalls/config.
Mirrors PelletConfig."""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive

from sqlalchemy import Column, DateTime, JSON, String

from app.database import Base


class RecallConfig(Base):
    __tablename__ = "recall_config"

    key        = Column(String(60), primary_key=True)
    value      = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=now_utc_naive,
                            onupdate=now_utc_naive, nullable=False)
    updated_by = Column(String(120), nullable=True)
