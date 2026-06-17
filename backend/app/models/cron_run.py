"""CronRun — a cross-instance claim ledger so a scheduled job runs at most
once per (job_name, run_key), even when several Cloud Run instances each run
their own in-process APScheduler. The unique PK makes the claim atomic: the
instance whose INSERT wins runs the job; the others see an IntegrityError and
skip. (See feedback_cloudrun_cron_reliability.)"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, String

from app.database import Base
from app.utils.dt import now_utc_naive


class CronRun(Base):
    __tablename__ = "cron_runs"

    job_name = Column(String(80), primary_key=True)
    run_key = Column(String(40), primary_key=True)   # e.g. the date "2026-06-17"
    claimed_at = Column(DateTime, default=now_utc_naive, nullable=False)
    claimed_by = Column(String(200), nullable=True)  # hostname/instance, best-effort
