"""Recall settings registry — every runtime-tunable recall value with its
default equal to the previously hardcoded workflow constant. Reads go through
cfg(); writes through PUT /recalls/config. Mirrors pellet/settings.py."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.recall_config import RecallConfig

log = logging.getLogger(__name__)

RECALL_SETTINGS_DEFAULTS: dict[str, Any] = {
    # recalls.CLAIM_TTL = timedelta(minutes=5)
    "claim_ttl_minutes":     5,
    # recalls dashboard overdue window: legacy timedelta(days=730) ≈ 24 months
    "overdue_window_months": 24,
    # recalls outcome taxonomy: PERMANENT_OUTCOMES / COOLDOWN_OUTCOMES /
    # COMPLETED_OUTCOMES + neutral "Wrong number", preserving order.
    "recall_outcomes": [
        {"label": "Declined recall",  "category": "permanent", "reason_code": "declined"},
        {"label": "Do not call",      "category": "permanent", "reason_code": "do_not_call"},
        {"label": "Patient deceased", "category": "permanent", "reason_code": "deceased"},
        {"label": "Left practice",    "category": "permanent", "reason_code": "left_practice"},
        {"label": "Left voicemail",   "category": "cooldown",  "cooldown_days": 3},
        {"label": "No answer",        "category": "cooldown",  "cooldown_days": 1},
        {"label": "Pending callback", "category": "cooldown",  "cooldown_days": 2},
        {"label": "Scheduled",        "category": "completed"},
        {"label": "Wrong number",     "category": "neutral"},
    ],
}


def cfg(db: Session, key: str) -> Any:
    """Read one setting: DB row if present, else registry default.
    Never raises for DB problems — falls back to the default."""
    if key not in RECALL_SETTINGS_DEFAULTS:
        raise KeyError(f"Unknown recall setting: {key}")
    try:
        row = db.query(RecallConfig).filter(RecallConfig.key == key).first()
        if row is not None and row.value is not None:
            return row.value
    except Exception:                                    # pragma: no cover
        log.warning("recall settings read failed for %s; using default", key)
    return RECALL_SETTINGS_DEFAULTS[key]
