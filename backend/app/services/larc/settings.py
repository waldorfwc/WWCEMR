"""LARC settings registry — every runtime-tunable LARC value with its default
equal to the previously hardcoded workflow constant. Reads go through cfg();
writes through PUT /larc/config. Mirrors surgery/settings.py."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.larc_config import LarcConfig

log = logging.getLogger(__name__)

LARC_SETTINGS_DEFAULTS: dict[str, Any] = {
    "device_expiry_hold_days":          365,   # workflow.DEVICE_EXPIRY_HOLD_DAYS
    "assignment_reallocate_after_days": 180,   # workflow.ASSIGNMENT_REALLOCATE_AFTER_DAYS
    "pharmacy_order_sla_days":           14,   # workflow.PHARMACY_ORDER_SLA_DAYS
    "checkout_ack_window_hours":         24,   # workflow.CHECKOUT_ACK_WINDOW_HOURS
}


def cfg(db: Session, key: str) -> Any:
    """Read one setting: DB row if present, else registry default.
    Never raises for DB problems — falls back to the default."""
    if key not in LARC_SETTINGS_DEFAULTS:
        raise KeyError(f"Unknown larc setting: {key}")
    try:
        row = db.query(LarcConfig).filter(LarcConfig.key == key).first()
        if row is not None and row.value is not None:
            return row.value
    except Exception:                                    # pragma: no cover
        log.warning("larc settings read failed for %s; using default", key)
    return LARC_SETTINGS_DEFAULTS[key]
