"""record_pellet_activity — one feed row per patient action, SAVEPOINT
soft-fail so it never poisons the caller's transaction. Mirrors
app/services/surgery/activity.py."""
from __future__ import annotations

import logging
from sqlalchemy.orm import Session
from app.models.pellet_portal import PelletActivity

log = logging.getLogger(__name__)


def record_pellet_activity(db: Session, patient, kind: str, summary: str,
                           actor: str = "patient", detail: str | None = None) -> None:
    try:
        with db.begin_nested():
            db.add(PelletActivity(
                pellet_patient_id=patient.id, kind=kind,
                summary=(summary or "")[:300], actor=actor, detail=detail))
    except Exception:                       # pragma: no cover - soft-fail
        log.exception("record_pellet_activity failed (kind=%s)", kind)
