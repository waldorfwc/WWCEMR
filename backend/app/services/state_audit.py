"""log_state_transition() — single helper for cross-module state audits.

Any place that mutates a meaningful state field (status, milestone state,
workflow stage, etc.) should call this. Cheap; one DB row per call.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.state_transition import StateTransitionAudit


def log_state_transition(
    db: Session,
    *,
    entity_type: str,
    entity_id: Any,
    action: str,
    actor: str,
    before: Optional[Any] = None,
    after: Optional[Any] = None,
    summary: Optional[str] = None,
    detail: Optional[dict] = None,
    flush: bool = True,
) -> StateTransitionAudit:
    """Write one immutable audit row. Caller is responsible for db.commit()
    — this helper just adds + flushes so the caller can attach FKs."""
    row = StateTransitionAudit(
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else "",
        action=action,
        before_value=(str(before) if before is not None else None),
        after_value=(str(after) if after is not None else None),
        actor=actor or "system",
        summary=summary,
        detail=detail,
    )
    db.add(row)
    if flush:
        db.flush()
    return row
