"""Recall settings registry: defaults merge with RecallConfig rows."""
import pytest

from app.services.recall.settings import RECALL_SETTINGS_DEFAULTS, cfg
from app.models.recall_config import RecallConfig


def test_defaults_present():
    assert RECALL_SETTINGS_DEFAULTS["claim_ttl_minutes"] == 5
    assert RECALL_SETTINGS_DEFAULTS["overdue_window_months"] == 24
    assert "recall_outcomes" in RECALL_SETTINGS_DEFAULTS
    outs = RECALL_SETTINGS_DEFAULTS["recall_outcomes"]
    labels = {o["label"] for o in outs}
    assert {"Declined recall", "Do not call", "Patient deceased",
            "Left practice", "Left voicemail", "No answer",
            "Pending callback", "Scheduled", "Wrong number"} <= labels


def test_defaults_match_legacy_constants():
    from app.routers import recalls
    # claim TTL
    assert RECALL_SETTINGS_DEFAULTS["claim_ttl_minutes"] == int(
        recalls.CLAIM_TTL.total_seconds() // 60)
    # taxonomy
    outs = RECALL_SETTINGS_DEFAULTS["recall_outcomes"]
    permanent = {o["label"]: o.get("reason_code")
                 for o in outs if o["category"] == "permanent"}
    cooldown = {o["label"]: o["cooldown_days"]
                for o in outs if o["category"] == "cooldown"}
    completed = {o["label"] for o in outs if o["category"] == "completed"}
    assert permanent == recalls.PERMANENT_OUTCOMES
    assert cooldown == {k: v.days for k, v in recalls.COOLDOWN_OUTCOMES.items()}
    assert completed == recalls.COMPLETED_OUTCOMES


def test_cfg_returns_default_when_no_row(db):
    assert cfg(db, "claim_ttl_minutes") == 5
    assert cfg(db, "overdue_window_months") == 24


def test_cfg_returns_db_override(db):
    db.add(RecallConfig(key="claim_ttl_minutes", value=10))
    db.commit()
    assert cfg(db, "claim_ttl_minutes") == 10


def test_cfg_unknown_key_raises():
    with pytest.raises(KeyError):
        cfg(None, "nope")
