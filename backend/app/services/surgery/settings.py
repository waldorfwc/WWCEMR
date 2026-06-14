"""Surgery settings registry.

Every runtime-tunable surgery value lives here with its default equal to
the previously hardcoded value, so a missing SurgeryConfig row always
means "behave exactly as before". Reads go through cfg(); writes go
through PUT /surgery/config (surgery_config.py), which validates against
this registry.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.surgery_config import SurgeryConfig

log = logging.getLogger(__name__)

SETTINGS_DEFAULTS: dict[str, Any] = {
    # ── pre-existing keys (moved from surgery_config.CONFIG_DEFAULTS) ──
    "office_full_threshold":     6,
    "office_lookahead_days":     6,
    "hospital_lookahead_days":  14,
    "reminder_lead_days":        [3, 1],

    # ── alerts & windows (previously hardcoded) ──
    "critical_overdue_hours":   48,    # surgery.py stuck-list red threshold
    "labs_alert_window_days":    7,    # surgery.py labs alert
    "post_op_docs_alert_days":   5,    # surgery.py op-notes alert
    "unresponsive_after_days":  30,    # was UNRESPONSIVE_AFTER_DAYS
    "preop_valid_days":        180,    # was PREOP_VALID_DAYS
    "schedule_horizon_days":   180,    # block_schedule materialization window
    "completed_window_days":    30,    # dashboard "completed last N days"

    # ── steps engine (consumed by step_engine.py in a later task) ──
    "step_expected_days_hospital": None,   # None → use catalog defaults
    "step_expected_days_office":   None,
    "step_titles_hospital":        None,
    "step_titles_office":          None,

    # ── structured configs (None → code defaults in their modules) ──
    "post_op_schedules":           None,   # post_op_schedule.py defaults
    "capacity_rules":              None,   # block_schedule.py defaults

    # ── intake option lists (editable in Surgery Settings) ──
    "clearance_types":             ["EKG", "Hematology", "Cardiology", "Pulmonology", "General"],
    "surgery_device_types":        ["Benesta", "Liletta", "Mirena", "Paragard", "Skyla", "Kyleena"],
}


def cfg(db: Session, key: str) -> Any:
    """Read one setting: DB row if present, else registry default.
    Never raises for DB problems — falls back to the default."""
    if key not in SETTINGS_DEFAULTS:
        raise KeyError(f"Unknown surgery setting: {key}")
    try:
        row = db.query(SurgeryConfig).filter(SurgeryConfig.key == key).first()
        if row is not None and row.value is not None:
            return row.value
    except Exception:                                    # pragma: no cover
        log.warning("surgery settings read failed for %s; using default", key)
    return SETTINGS_DEFAULTS[key]
