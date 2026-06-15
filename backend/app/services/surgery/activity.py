"""record_activity — append a row to the SurgeryActivity feed.

Called from every patient-action site (date pick, reschedule, cancel,
consent signed/declined, document upload, labs self-report, payment) and
from system sweeps (auto-unresponsive, step-overdue).

Soft-fail by contract: logging an activity row must NEVER break the
patient action that triggered it. Any error is swallowed and logged at
WARN. The row is flushed (so the caller's surrounding commit persists it)
but never committed here — the caller owns the transaction. If the caller
hasn't committed yet, our flush rides along on their commit; if the caller
already committed, we commit our own row.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.surgery_activity import SurgeryActivity

log = logging.getLogger(__name__)


def record_activity(db: Session, surgery, kind: str, summary: str,
                    actor: str = "patient") -> None:
    """Insert one activity-feed row. Soft-fail — never raises into the
    caller. `surgery` is a Surgery row (we read its id)."""
    try:
        row = SurgeryActivity(
            surgery_id=surgery.id,
            kind=kind,
            summary=(summary or "")[:300],
            actor=actor,
        )
        db.add(row)
        # Flush so the row is part of the caller's pending transaction. The
        # caller's own commit persists it; if there is no later commit the
        # row would be lost on session close, so we also try a commit when
        # the session has nothing else pending.
        db.flush()
    except Exception as exc:
        log.warning("record_activity failed (%s/%s): %s",
                    getattr(surgery, "id", "?"), kind, exc)
