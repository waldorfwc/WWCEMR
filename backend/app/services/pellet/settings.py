"""Pellet settings registry — every runtime-tunable pellet value with its
default equal to the previously hardcoded workflow constant. Reads go through
cfg(); writes through PUT /pellets/config. Mirrors larc/settings.py."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.pellet_config import PelletConfig

log = logging.getLogger(__name__)

PELLET_SETTINGS_DEFAULTS: dict[str, Any] = {
    "stale_visit_days":         7,    # pellet/stale_sweep.STALE_DAYS
    "dose_suggest_max_pellets": 12,   # pellet/dose_suggest.MAX_PELLETS
    "dose_suggest_max_results": 6,    # pellet/dose_suggest.MAX_RESULTS
    "labs_valid_days":          14,   # labs must be drawn within N days of the visit
    "mammo_valid_days":         365,  # mammo must be within N days of the visit
}


def cfg(db: Session, key: str) -> Any:
    """Read one setting: DB row if present, else registry default.
    Never raises for DB problems — falls back to the default."""
    if key not in PELLET_SETTINGS_DEFAULTS:
        raise KeyError(f"Unknown pellet setting: {key}")
    try:
        row = db.query(PelletConfig).filter(PelletConfig.key == key).first()
        if row is not None and row.value is not None:
            return row.value
    except Exception:                                    # pragma: no cover
        log.warning("pellet settings read failed for %s; using default", key)
    return PELLET_SETTINGS_DEFAULTS[key]
