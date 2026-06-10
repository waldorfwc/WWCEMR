"""Auto-Unresponsive sweep — closes audit finding #13.

Rule: a surgery is marked Unresponsive when the patient has not
engaged in N days (default 30) past either their pre-op visit or their
last portal action. The pre-op visit is when the surgeon reviewed
expectations and risks; a patient who hasn't picked a date 30 days
later has effectively walked away.

Why this exists: the rule was documented in surgery.py (UNRESPONSIVE_AFTER_DAYS)
and surfaced as a dashboard *bucket*, but no job ever wrote
status='unresponsive' to the row. Cases languished in 'in_progress'
forever, and the bucket-only signal raced with late patient activity.
This sweep is the durable transition.

Idempotency: the sweep skips rows already in status='unresponsive' or
where auto_unresponsive_at is already set, so re-running the cron is
safe. Each row is processed in its own transaction so one bad row
doesn't poison the batch. Surgery.version_id enforces optimistic
locking — if a coordinator just patched the row, the auto-transition
aborts with StaleDataError and the row is retried next sweep.
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime, timedelta
from app.utils.dt import now_utc_naive
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.database import SessionLocal
from app.models.surgery import Surgery, SurgeryCancellation
# Force SurgeryPayment + other related mappers to register so SQLAlchemy
# can resolve the string-based relationships defined on Surgery. The
# Cloud Run Job entry point doesn't pull these in via the FastAPI
# router graph the way the web service does.
from app.models import stripe_payment as _stripe_payment_models  # noqa: F401

log = logging.getLogger(__name__)

# Has to match UNRESPONSIVE_AFTER_DAYS in routers/surgery.py — the
# bucket rule and the transition rule must agree.
UNRESPONSIVE_AFTER_DAYS = 30


def _effective_engagement_anchor(s: Surgery) -> Optional[_date]:
    """Latest of preop_date and last_patient_activity_at (as a date).

    Returns None when neither is set — the case isn't yet eligible for
    auto-transition. The sweep skips those rows."""
    candidates: list[_date] = []
    if s.preop_date:
        candidates.append(s.preop_date)
    if s.last_patient_activity_at:
        candidates.append(s.last_patient_activity_at.date())
    if not candidates:
        return None
    return max(candidates)


def find_candidates(db: Session, *, today: Optional[_date] = None) -> list[Surgery]:
    """Surgeries due for auto-Unresponsive marking today.

    Eligible when:
      - preop_date is set and in the past
      - scheduled_date is still null
      - status is not already terminal/closed
      - the effective engagement anchor (max of preop_date and
        last_patient_activity_at) is >= UNRESPONSIVE_AFTER_DAYS old
    """
    today = today or _date.today()
    cutoff = today - timedelta(days=UNRESPONSIVE_AFTER_DAYS)
    candidates = (db.query(Surgery)
                    .filter(Surgery.preop_date.isnot(None),
                            Surgery.preop_date <= cutoff,
                            Surgery.scheduled_date.is_(None),
                            Surgery.status.notin_(
                                ("cancelled", "completed", "unresponsive")),
                            Surgery.auto_unresponsive_at.is_(None))
                    .all())
    return [s for s in candidates
            if (_effective_engagement_anchor(s) or today) <= cutoff]


def mark_unresponsive(db: Session, s: Surgery, *, by: str) -> bool:
    """Transition one surgery to status='unresponsive'. Returns True on
    success, False on StaleDataError (caller can move on; the row will
    be picked up by the next sweep)."""
    s.status = "unresponsive"
    s.auto_unresponsive_at = now_utc_naive()
    notes = (
        f"Auto-transitioned to Unresponsive by the daily sweep — "
        f"30+ days past pre-op (preop_date={s.preop_date}) with no "
        f"scheduled_date and no portal activity. Reach out by phone "
        f"if the practice wants to re-engage the patient."
    )
    db.add(SurgeryCancellation(
        surgery_id=s.id,
        cancelled_by=by,
        reason="unresponsive",
        fee_required=False,
        refund_required=bool(s.amount_paid and float(s.amount_paid) > 0),
        notes=notes,
    ))
    try:
        db.commit()
    except StaleDataError:
        # A coordinator just patched the row — Surgery.version_id
        # changed under us. Roll back and let the next sweep retry.
        db.rollback()
        log.info("Auto-unresponsive aborted on Surgery %s: row was "
                 "concurrently updated; will retry next sweep", s.id)
        return False
    return True


def run_auto_unresponsive_sweep() -> dict:
    """Cloud Run Job entry point. Marks every eligible Surgery
    Unresponsive and returns a counter dict."""
    db = SessionLocal()
    swept = 0
    skipped = 0
    try:
        cands = find_candidates(db)
        log.info("Auto-unresponsive sweep: %s candidate(s)", len(cands))
        for s in cands:
            ok = mark_unresponsive(db, s, by="system:auto-unresponsive")
            if ok:
                swept += 1
            else:
                skipped += 1
        log.info("Auto-unresponsive sweep done: %s swept, %s skipped",
                 swept, skipped)
        return {"swept": swept, "skipped": skipped, "candidates": len(cands)}
    finally:
        db.close()
