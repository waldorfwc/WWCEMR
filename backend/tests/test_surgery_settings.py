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
                        "slot_times": ["07:30", "08:30"]}}
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


# ─── #7 partial-dict save must MERGE, not wipe ──────────────────────

def test_put_config_merges_step_expected_days(client):
    r = client.put("/api/surgery/config",
                   json={"step_expected_days_hospital": {"benefits": 5}})
    assert r.status_code == 200
    r = client.put("/api/surgery/config",
                   json={"step_expected_days_hospital": {"payment": 9}})
    assert r.status_code == 200
    got = client.get("/api/surgery/config").json()["step_expected_days_hospital"]
    assert got["benefits"] == 5
    assert got["payment"] == 9


def test_put_config_merges_step_titles(client):
    client.put("/api/surgery/config",
               json={"step_titles_office": {"benefits": "Benefits Check"}})
    client.put("/api/surgery/config",
               json={"step_titles_office": {"payment": "Collect Payment"}})
    got = client.get("/api/surgery/config").json()["step_titles_office"]
    assert got["benefits"] == "Benefits Check"
    assert got["payment"] == "Collect Payment"


def test_put_config_merges_capacity_rules_by_facility(client):
    a = {"medstar": {"kind": "robotic",
                     "options": [{"case_kind": "robotic_180", "max": 3}]}}
    b = {"crmc": {"kind": "mix_exclusive",
                  "options": [{"case_kind": "minor", "max": 6}]}}
    assert client.put("/api/surgery/config", json={"capacity_rules": a}).status_code == 200
    assert client.put("/api/surgery/config", json={"capacity_rules": b}).status_code == 200
    got = client.get("/api/surgery/config").json()["capacity_rules"]
    assert "medstar" in got and got["medstar"]["kind"] == "robotic"
    assert "crmc" in got and got["crmc"]["kind"] == "mix_exclusive"


# ─── #8 reminder_lead_days validation ───────────────────────────────

def test_reminder_lead_days_rejects_empty(client):
    r = client.put("/api/surgery/config", json={"reminder_lead_days": []})
    assert r.status_code == 422


def test_reminder_lead_days_accepts_valid(client):
    r = client.put("/api/surgery/config", json={"reminder_lead_days": [3, 1]})
    assert r.status_code == 200
    assert client.get("/api/surgery/config").json()["reminder_lead_days"] == [3, 1]


def test_reminder_lead_days_rejects_zero(client):
    r = client.put("/api/surgery/config", json={"reminder_lead_days": [0]})
    assert r.status_code == 422


def test_reminder_lead_days_rejects_over_max(client):
    r = client.put("/api/surgery/config", json={"reminder_lead_days": [100]})
    assert r.status_code == 422


# ─── #27 capacity options must be non-empty for count kinds ─────────

def test_capacity_rules_rejects_empty_options(client):
    bad = {"medstar": {"kind": "robotic", "options": []}}
    r = client.put("/api/surgery/config", json={"capacity_rules": bad})
    assert r.status_code == 422


def test_capacity_rules_accepts_options_present(client):
    good = {"medstar": {"kind": "robotic",
                        "options": [{"case_kind": "robotic_180", "max": 3}]}}
    r = client.put("/api/surgery/config", json={"capacity_rules": good})
    assert r.status_code == 200


# ─── #29 case_minutes is gone from the schema ───────────────────────

def test_capacity_rules_has_no_case_minutes_field(client):
    from app.routers.surgery_config import FacilityCapacity
    assert "case_minutes" not in FacilityCapacity.model_fields
    # A PUT that includes case_minutes is accepted (extra field ignored),
    # not stored as a meaningful setting.
    r = client.put("/api/surgery/config", json={"capacity_rules": {
        "office": {"kind": "fixed_slots",
                   "slot_times": ["07:30", "08:30"],
                   "case_minutes": 60}}})
    assert r.status_code == 200
