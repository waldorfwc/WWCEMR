"""LARC (device) module config table — KV store backing /larc/settings.
Mirrors SurgeryConfig."""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive

from sqlalchemy import Column, DateTime, JSON, String

from app.database import Base


class LarcConfig(Base):
    __tablename__ = "larc_config"

    key        = Column(String(60), primary_key=True)
    value      = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=now_utc_naive,
                            onupdate=now_utc_naive, nullable=False)
    updated_by = Column(String(120), nullable=True)
