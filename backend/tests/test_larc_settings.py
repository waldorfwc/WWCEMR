"""LARC settings registry: defaults merge with LarcConfig rows."""
from app.services.larc.settings import LARC_SETTINGS_DEFAULTS, cfg
from app.models.larc_config import LarcConfig


def test_defaults_match_workflow_constants():
    from app.services.larc import workflow as wf
    assert LARC_SETTINGS_DEFAULTS["device_expiry_hold_days"] == wf.DEVICE_EXPIRY_HOLD_DAYS
    assert LARC_SETTINGS_DEFAULTS["assignment_reallocate_after_days"] == wf.ASSIGNMENT_REALLOCATE_AFTER_DAYS
    assert LARC_SETTINGS_DEFAULTS["pharmacy_order_sla_days"] == wf.PHARMACY_ORDER_SLA_DAYS
    assert LARC_SETTINGS_DEFAULTS["checkout_ack_window_hours"] == wf.CHECKOUT_ACK_WINDOW_HOURS


def test_cfg_returns_default_when_no_row(db):
    assert cfg(db, "pharmacy_order_sla_days") == 14


def test_cfg_returns_db_override(db):
    db.add(LarcConfig(key="pharmacy_order_sla_days", value=21))
    db.commit()
    assert cfg(db, "pharmacy_order_sla_days") == 21


def test_cfg_unknown_key_raises():
    import pytest
    with pytest.raises(KeyError):
        cfg(None, "nope")
