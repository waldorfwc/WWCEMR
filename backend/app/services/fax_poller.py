"""Poll RingCentral for outstanding fax statuses and update FaxLog rows."""
import os
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler

from app.database import SessionLocal
from app.models.fax_log import FaxLog, FaxLogStatus
from app.services.fax_service import check_fax_status
from app.services.audit_service import log_action

POLL_INTERVAL_MINUTES = int(os.environ.get("FAX_POLL_INTERVAL_MINUTES", "2"))
POLL_MAX_AGE_MINUTES = int(os.environ.get("FAX_POLL_MAX_AGE_MINUTES", "60"))


# RingCentral statuses → our FaxLogStatus
_DELIVERED_STATES = {"Sent", "Delivered", "Received"}
_FAILED_STATES = {"SendingFailed", "DeliveryFailed", "Failed"}
_IN_FLIGHT_STATES = {"Queued", "Sending"}


def poll_outstanding_faxes(db: Session) -> int:
    """One polling pass. Returns the number of rows whose status transitioned."""
    cutoff = datetime.utcnow() - timedelta(minutes=POLL_MAX_AGE_MINUTES)
    candidates = (
        db.query(FaxLog)
        .filter(
            FaxLog.status.in_([FaxLogStatus.QUEUED, FaxLogStatus.SENT]),
            FaxLog.sent_at >= cutoff,
            FaxLog.ringcentral_message_id.isnot(None),
        )
        .all()
    )

    changed = 0
    now = datetime.utcnow()
    for row in candidates:
        try:
            rc = check_fax_status(row.ringcentral_message_id)
        except Exception as e:
            # Don't fail the batch; mark last_checked_at and continue
            row.last_checked_at = now
            db.commit()
            continue

        rc_status = (rc.get("status") or "").strip() if rc else ""
        row.last_checked_at = now

        if rc_status in _DELIVERED_STATES:
            if row.status != FaxLogStatus.DELIVERED:
                row.status = FaxLogStatus.DELIVERED
                row.delivered_at = now
                changed += 1
                log_action(db, "FAX_DELIVERED", "fax", resource_id=str(row.id),
                           description=f"Fax {row.ringcentral_message_id} delivered")
        elif rc_status in _FAILED_STATES:
            if row.status != FaxLogStatus.FAILED:
                row.status = FaxLogStatus.FAILED
                row.error = rc.get("error") or rc_status
                changed += 1
                log_action(db, "FAX_FAILED", "fax", resource_id=str(row.id),
                           description=f"Fax {row.ringcentral_message_id} failed: {row.error}")
        # In-flight / unknown → leave status alone

        db.commit()

    return changed


def _tick():
    db = SessionLocal()
    try:
        poll_outstanding_faxes(db)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(_tick, "interval", minutes=POLL_INTERVAL_MINUTES, id="fax_poller",
                  max_instances=1, coalesce=True)
    sched.start()
    return sched
