"""Settings registry: defaults merge with SurgeryConfig rows."""
import pytest
from app.services.surgery.settings import SETTINGS_DEFAULTS, cfg
from app.models.surgery_config import SurgeryConfig


def test_defaults_match_legacy_hardcoded_values():
    assert SETTINGS_DEFAULTS["critical_overdue_hours"] == 48
    assert SETTINGS_DEFAULTS["labs_alert_window_days"] == 7
    assert SETTINGS_DEFAULTS["post_op_docs_alert_days"] == 5
    assert SETTINGS_DEFAULTS["unresponsive_after_days"] == 30
    assert SETTINGS_DEFAULTS["preop_valid_days"] == 180
    assert SETTINGS_DEFAULTS["schedule_horizon_days"] == 180
    assert SETTINGS_DEFAULTS["completed_window_days"] == 30
    # pre-existing keys keep working through the same registry
    assert SETTINGS_DEFAULTS["office_full_threshold"] == 6
    assert SETTINGS_DEFAULTS["reminder_lead_days"] == [3, 1]


def test_cfg_returns_default_when_no_row(db):
    assert cfg(db, "critical_overdue_hours") == 48


def test_cfg_returns_db_override(db):
    db.add(SurgeryConfig(key="critical_overdue_hours", value=72))
    db.commit()
    assert cfg(db, "critical_overdue_hours") == 72


def test_cfg_unknown_key_raises():
    with pytest.raises(KeyError):
        cfg(None, "no_such_key")
