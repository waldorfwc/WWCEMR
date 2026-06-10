"""Simple key/value practice-wide settings (ema fax number, labels, etc.)."""
from sqlalchemy import Column, String, DateTime
from sqlalchemy.orm import Session
from datetime import datetime
from app.utils.dt import now_utc_naive
from typing import Optional
from app.database import Base


class PracticeConfig(Base):
    __tablename__ = "practice_config"

    key = Column(String(80), primary_key=True)
    value = Column(String(500), nullable=True)
    updated_at = Column(DateTime, default=now_utc_naive, onupdate=now_utc_naive)


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.query(PracticeConfig).filter(PracticeConfig.key == key).first()
    return row.value if row else default
