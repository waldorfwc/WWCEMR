"""Post-op schedule rules: config-driven with hardcoded fallback parity."""
from types import SimpleNamespace
from app.services.post_op_schedule import (
    determine_post_op_schedule, rules_from_config, DEFAULT_PROCEDURE_RULES,
)
from app.models.surgery_config import SurgeryConfig


def _surgery(desc):
    return SimpleNamespace(procedures=[{"description": desc}])


def test_default_parity_hysterectomy(db):
    visits = determine_post_op_schedule(_surgery("Robotic hysterectomy"), db=db)
    assert [(v.days_post_op, v.suggested_location) for v in visits] == \
        [(7, "office"), (42, "office")]
    assert visits[1].location_locked is True


def test_config_override_changes_offsets(db):
    db.add(SurgeryConfig(key="post_op_schedules", value=[
        {"match": ["hysterectomy"], "visits": [
            {"label": "10 days post-op", "offset_days": 10, "mode": "office"}]},
    ]))
    db.commit()
    visits = determine_post_op_schedule(_surgery("Robotic hysterectomy"), db=db)
    assert [(v.days_post_op,) for v in visits] == [(10,)]


def test_no_db_falls_back_to_defaults():
    visits = determine_post_op_schedule(_surgery("LEEP"), db=None)
    assert [(v.days_post_op, v.location_locked) for v in visits] == [(14, True)]


def test_malformed_config_falls_back(db):
    db.add(SurgeryConfig(key="post_op_schedules", value=[{"bad": "shape"}]))
    db.commit()
    # should not raise; falls back to defaults (hysterectomy → 7,42)
    visits = determine_post_op_schedule(_surgery("hysterectomy"), db=db)
    assert [v.days_post_op for v in visits] == [7, 42]
