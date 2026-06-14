"""R2 parity: the config-driven taxonomy reproduces the legacy hardcoded
behaviour with no config rows, and reflects overrides when rows exist."""
from datetime import timedelta

from app.routers.recalls import (
    _taxonomy, PERMANENT_OUTCOMES, COOLDOWN_OUTCOMES, COMPLETED_OUTCOMES,
)
from app.models.recall import RecallEntry
from app.models.recall_config import RecallConfig
from app.utils.dt import now_utc_naive


def _make_entry(db, chart="REC-1"):
    e = RecallEntry(chart_number=chart, status="active")
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def test_default_taxonomy_matches_legacy(db):
    permanent, cooldown, completed, all_labels = _taxonomy(db)
    assert permanent == PERMANENT_OUTCOMES
    assert cooldown == COOLDOWN_OUTCOMES
    assert completed == COMPLETED_OUTCOMES
    assert "Wrong number" in all_labels
    # cooldown durations
    assert cooldown["Left voicemail"] == timedelta(days=3)
    assert cooldown["No answer"] == timedelta(days=1)
    assert cooldown["Pending callback"] == timedelta(days=2)


def test_record_cooldown_outcome_default(client, db):
    e = _make_entry(db, "REC-CD")
    r = client.post(f"/api/recalls/{e.id}/outcome",
                    json={"outcome": "Left voicemail"})
    assert r.status_code == 200, r.text
    db.refresh(e)
    expected = now_utc_naive() + timedelta(days=3)
    assert e.cooldown_until is not None
    assert abs((e.cooldown_until - expected).total_seconds()) < 120


def test_override_cooldown_days_changes_recorded_cooldown(client, db):
    # Override "No answer" cooldown from 1 -> 5 days.
    outs = [
        {"label": "Declined recall",  "category": "permanent", "reason_code": "declined"},
        {"label": "Do not call",      "category": "permanent", "reason_code": "do_not_call"},
        {"label": "Patient deceased", "category": "permanent", "reason_code": "deceased"},
        {"label": "Left practice",    "category": "permanent", "reason_code": "left_practice"},
        {"label": "Left voicemail",   "category": "cooldown",  "cooldown_days": 3},
        {"label": "No answer",        "category": "cooldown",  "cooldown_days": 5},
        {"label": "Pending callback", "category": "cooldown",  "cooldown_days": 2},
        {"label": "Scheduled",        "category": "completed"},
        {"label": "Wrong number",     "category": "neutral"},
    ]
    db.add(RecallConfig(key="recall_outcomes", value=outs))
    db.commit()

    e = _make_entry(db, "REC-NA")
    r = client.post(f"/api/recalls/{e.id}/outcome", json={"outcome": "No answer"})
    assert r.status_code == 200, r.text
    db.refresh(e)
    expected = now_utc_naive() + timedelta(days=5)
    assert abs((e.cooldown_until - expected).total_seconds()) < 120


def test_catalog_default_matches_legacy(client):
    r = client.get("/api/recalls/outcomes/catalog")
    assert r.status_code == 200
    by_value = {o["value"]: o for o in r.json()["outcomes"]}
    assert by_value["Declined recall"]["permanent_suppression"] is True
    assert by_value["Scheduled"]["completes_recall"] is True
    assert by_value["Left voicemail"]["cooldown_days"] == 3
    assert by_value["No answer"]["cooldown_days"] == 1
    assert by_value["Wrong number"]["permanent_suppression"] is False
    assert by_value["Wrong number"]["completes_recall"] is False
    assert by_value["Wrong number"]["cooldown_days"] is None


def test_catalog_reflects_override(client, db):
    outs = [
        {"label": "No answer", "category": "cooldown", "cooldown_days": 9},
        {"label": "Scheduled", "category": "completed"},
    ]
    db.add(RecallConfig(key="recall_outcomes", value=outs))
    db.commit()
    r = client.get("/api/recalls/outcomes/catalog")
    by_value = {o["value"]: o for o in r.json()["outcomes"]}
    assert set(by_value) == {"No answer", "Scheduled"}
    assert by_value["No answer"]["cooldown_days"] == 9
