"""Cross-instance single-flight guard for scheduled jobs.

The backend runs crons via an in-process APScheduler in each Cloud Run
instance (see feedback_cloudrun_cron_reliability). Idempotent jobs (e.g.
pellet slot materialization) are fine to fire on every instance, but jobs
that send external email/Slack must run once. `claim_cron_run` claims a
(job_name, run_key) atomically via the cron_runs PK; only the winning
instance proceeds.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.cron_run import CronRun
from app.utils.dt import now_utc_naive

log = logging.getLogger(__name__)


def claim_cron_run(db: Session, job_name: str, run_key: str) -> bool:
    """Atomically claim one run of `job_name` for `run_key` across instances.

    Returns True if THIS caller won the claim (it should run the job), False
    if another instance already claimed it. Commits the claim row on success;
    rolls back cleanly on a lost race. Never raises for the race itself.
    """
    db.add(CronRun(job_name=job_name, run_key=run_key,
                   claimed_at=now_utc_naive(),
                   claimed_by=(os.environ.get("HOSTNAME") or None)))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        log.info("cron %s run_key=%s already claimed by another instance — skipping",
                 job_name, run_key)
        return False
    except Exception:
        # Any other DB error: don't block the job, but don't double-guard.
        db.rollback()
        log.exception("claim_cron_run(%s, %s) failed; running anyway", job_name, run_key)
        return True
