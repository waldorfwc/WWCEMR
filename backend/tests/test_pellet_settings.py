"""Pellet settings registry: defaults merge with PelletConfig rows."""
from app.services.pellet.settings import PELLET_SETTINGS_DEFAULTS, cfg
from app.models.pellet_config import PelletConfig


def test_defaults_match_constants():
    from app.services.pellet import stale_sweep, dose_suggest
    assert PELLET_SETTINGS_DEFAULTS["stale_visit_days"] == stale_sweep.STALE_DAYS
    assert PELLET_SETTINGS_DEFAULTS["dose_suggest_max_pellets"] == dose_suggest.MAX_PELLETS
    assert PELLET_SETTINGS_DEFAULTS["dose_suggest_max_results"] == dose_suggest.MAX_RESULTS


def test_cfg_returns_default_when_no_row(db):
    assert cfg(db, "stale_visit_days") == 7


def test_cfg_returns_db_override(db):
    db.add(PelletConfig(key="stale_visit_days", value=14))
    db.commit()
    assert cfg(db, "stale_visit_days") == 14


def test_cfg_unknown_key_raises():
    import pytest
    with pytest.raises(KeyError):
        cfg(None, "nope")
