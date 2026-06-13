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


def test_put_config_accepts_new_scalar(client):
    r = client.put("/api/surgery/config", json={"critical_overdue_hours": 72})
    assert r.status_code == 200
    got = client.get("/api/surgery/config").json()
    assert got["critical_overdue_hours"] == 72


def test_put_config_rejects_out_of_range(client):
    r = client.put("/api/surgery/config", json={"critical_overdue_hours": -1})
    assert r.status_code == 422


def test_put_config_rejects_bad_capacity_rules(client):
    bad = {"medstar": {"kind": "robotic", "options": [
        {"case_kind": "robotic_180", "max": 99}]}}   # max out of range
    r = client.put("/api/surgery/config", json={"capacity_rules": bad})
    assert r.status_code == 422


def test_put_config_accepts_valid_capacity_rules(client):
    good = {"office": {"kind": "fixed_slots",
                        "slot_times": ["07:30", "08:30"],
                        "case_minutes": 60}}
    r = client.put("/api/surgery/config", json={"capacity_rules": good})
    assert r.status_code == 200


def test_put_config_accepts_post_op_schedules(client):
    rules = [{"match": ["hysterectomy"], "visits": [
        {"label": "1 week post-op", "offset_days": 7, "mode": "office"}]}]
    r = client.put("/api/surgery/config", json={"post_op_schedules": rules})
    assert r.status_code == 200


def test_put_config_rejects_bad_step_days(client):
    r = client.put("/api/surgery/config",
                   json={"step_expected_days_hospital": {"benefits": 999}})
    assert r.status_code == 422
