# Surgery Settings + Steps Engine + Dead-Code Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the surgery module's hardcoded business values runtime-configurable via a new `/surgery/settings` page, cut the dashboard's behind-schedule/Critical-Alerts engine over from retired milestones to a server-side Steps engine, and remove DocuSign + klara_scheduling dead code.

**Architecture:** A defaults registry (`app/services/surgery/settings.py`) backs every config key with today's hardcoded value; the existing `SurgeryConfig` KV table overrides per key. A new pure-functional `step_engine.py` ports the frontend's step-completion logic, becomes the single source of truth (serializer emits `steps`), and replaces all milestone reads. Frontend gets a 5-tab settings page; `/surgery/rules` redirects there.

**Tech Stack:** FastAPI + SQLAlchemy + Pydantic (backend), React + react-query + Tailwind (frontend), pytest, Cloud Run via `gcloud builds submit --tag=...`.

**Spec:** `docs/superpowers/specs/2026-06-12-surgery-settings-design.md`

**Conventions (project memories):** Title Case for UI section titles/buttons; MM/DD/YYYY dates via `fmt.date()`; no secrets in source; never `--config=cloudbuild.yaml`.

**Working directory:** backend commands run from `backend/` using `venv/bin/python`. Frontend from `frontend/`.

---

## Phase A — Settings backend

### Task 1: Settings defaults registry

**Files:**
- Create: `backend/app/services/surgery/settings.py`
- Modify: `backend/app/routers/surgery_config.py:29-38` (CONFIG_DEFAULTS moves to registry)
- Test: `backend/tests/test_surgery_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_surgery_settings.py
"""Settings registry: defaults merge with SurgeryConfig rows."""
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


def test_cfg_returns_default_when_no_row(db_session):
    assert cfg(db_session, "critical_overdue_hours") == 48


def test_cfg_returns_db_override(db_session):
    db_session.add(SurgeryConfig(key="critical_overdue_hours", value=72))
    db_session.commit()
    assert cfg(db_session, "critical_overdue_hours") == 72


def test_cfg_unknown_key_raises():
    import pytest
    with pytest.raises(KeyError):
        cfg(None, "no_such_key")
```

(Use the existing `db_session` fixture from `backend/tests/conftest.py` — check its exact name first with `grep -n "def db" tests/conftest.py` and adjust.)

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_surgery_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.surgery.settings`

- [ ] **Step 3: Write the registry**

```python
# backend/app/services/surgery/settings.py
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

    # ── steps engine (Task 6 consumes; defaults defined in step_engine) ──
    "step_expected_days_hospital": None,   # None → use catalog defaults
    "step_expected_days_office":   None,
    "step_titles_hospital":        None,
    "step_titles_office":          None,

    # ── structured configs (None → code defaults in their modules) ──
    "post_op_schedules":           None,   # post_op_schedule.py defaults
    "capacity_rules":              None,   # block_schedule.py defaults
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
```

- [ ] **Step 4: Point surgery_config.py at the registry**

In `backend/app/routers/surgery_config.py`, replace the `CONFIG_DEFAULTS = {...}` block (lines 29-36) with:

```python
from app.services.surgery.settings import SETTINGS_DEFAULTS as CONFIG_DEFAULTS
```

(`_read_config` and `put_config` keep working unchanged — they iterate `CONFIG_DEFAULTS`.)

- [ ] **Step 5: Run tests**

Run: `venv/bin/python -m pytest tests/test_surgery_settings.py -v`
Expected: 4 PASS

Run: `venv/bin/python -m pytest tests/ -k "config or surgery" -q`
Expected: no new failures vs. baseline (run baseline first if unsure).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/surgery/settings.py backend/app/routers/surgery_config.py backend/tests/test_surgery_settings.py
git commit -m "feat(surgery-settings): settings registry with legacy-value defaults"
```

---

### Task 2: Thread scalar settings through read sites

**Files:**
- Modify: `backend/app/routers/surgery.py` (lines ~331, ~364, ~501-520, ~583, ~596-612, ~652, ~386, ~751)
- Modify: `backend/app/services/surgery/block_schedule.py:94`

- [ ] **Step 1: Replace constants in surgery.py**

At `surgery.py:501-505`, replace:

```python
UNRESPONSIVE_AFTER_DAYS = 30
PREOP_VALID_DAYS = 180
```

with module-level removal — delete both constants. Then at each use site, read from settings (each of these functions already has `db` in scope; verify with the surrounding code and pass `db` where needed):

- Line ~520 (`(s.scheduled_date - s.preop_date).days > PREOP_VALID_DAYS`) → `> cfg(db, "preop_valid_days")`
- Line ~583 (`>= UNRESPONSIVE_AFTER_DAYS`) → `>= cfg(db, "unresponsive_after_days")`
- Line ~596 (repeat 180d check) → `cfg(db, "preop_valid_days")`
- Line ~331 (`today - timedelta(days=30)`) → `timedelta(days=cfg(db, "completed_window_days"))`
- Line ~364 (`if hrs > 48`) → `if hrs > cfg(db, "critical_overdue_hours")`
- Line ~602 (`0 <= days_until <= 7`) → `<= cfg(db, "labs_alert_window_days")`
- Line ~612 (`days_since >= 5`) → `>= cfg(db, "post_op_docs_alert_days")`
- Line ~652 (`age - m.expected_duration_days > 2`) → `> cfg(db, "critical_overdue_hours") / 24` — **note:** this whole function is replaced in Task 7; make the cfg change anyway so Task 2 is complete standalone.
- Line ~386 (`.limit(180)`) → `.limit(cfg(db, "schedule_horizon_days"))`

Add the import near the top of surgery.py:

```python
from app.services.surgery.settings import cfg
```

If a helper function uses one of these values but has no `db` parameter, add `db: Session` as a parameter and update its callers (they all have `db` from FastAPI dependencies). Do NOT cache cfg values at module import time.

- [ ] **Step 2: block_schedule horizon**

`block_schedule.py:94` — change the signature default to a sentinel and resolve inside:

```python
def materialize_block_days(db: Session, days_ahead: int | None = None, ...):
    if days_ahead is None:
        from app.services.surgery.settings import cfg
        days_ahead = cfg(db, "schedule_horizon_days")
```

(Match the real function name/signature at that line — read it first.)

- [ ] **Step 3: Verify nothing else references the deleted constants**

Run: `grep -rn "UNRESPONSIVE_AFTER_DAYS\|PREOP_VALID_DAYS" app/ tests/`
Expected: no hits in `app/`; update any test that referenced them to use the registry default instead.

- [ ] **Step 4: Run the surgery test suite**

Run: `venv/bin/python -m pytest tests/ -k surgery -q`
Expected: same pass/fail as pre-change baseline.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/surgery.py backend/app/services/surgery/block_schedule.py
git commit -m "feat(surgery-settings): alert thresholds and windows read from config"
```

---

### Task 3: Validated PUT /surgery/config for all new keys

**Files:**
- Modify: `backend/app/routers/surgery_config.py:43-49` (ConfigPayload) and `put_config`
- Test: `backend/tests/test_surgery_settings.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_surgery_settings.py` (use the project's existing API test client fixture — see `tests/conftest.py`; most API tests use a `client` fixture with an auth header helper; mirror a nearby test like `test_admin_tiers_api.py` for the exact auth pattern):

```python
def test_put_config_accepts_new_scalar(client, manage_auth_headers):
    r = client.put("/api/surgery/config",
                   json={"critical_overdue_hours": 72},
                   headers=manage_auth_headers)
    assert r.status_code == 200
    assert client.get("/api/surgery/config",
                      headers=manage_auth_headers).json()["critical_overdue_hours"] == 72


def test_put_config_rejects_out_of_range(client, manage_auth_headers):
    r = client.put("/api/surgery/config",
                   json={"critical_overdue_hours": -1},
                   headers=manage_auth_headers)
    assert r.status_code == 422


def test_put_config_rejects_bad_capacity_rules(client, manage_auth_headers):
    bad = {"medstar": {"kind": "robotic", "options": [
        {"case_kind": "robotic_180", "max": 99}]}}   # max out of range
    r = client.put("/api/surgery/config", json={"capacity_rules": bad},
                   headers=manage_auth_headers)
    assert r.status_code == 422


def test_put_config_accepts_valid_capacity_rules(client, manage_auth_headers):
    good = {"office": {"kind": "fixed_slots",
                        "slot_times": ["07:30", "08:30"],
                        "case_minutes": 60}}
    r = client.put("/api/surgery/config", json={"capacity_rules": good},
                   headers=manage_auth_headers)
    assert r.status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_surgery_settings.py -v`
Expected: new tests FAIL (422 missing / field rejected as unknown).

- [ ] **Step 3: Extend ConfigPayload with validation**

In `surgery_config.py`, replace `ConfigPayload` with:

```python
import re
from pydantic import field_validator

class CapacityOption(BaseModel):
    case_kind: str
    max: int = Field(ge=1, le=20)

    @field_validator("case_kind")
    @classmethod
    def known_kind(cls, v):
        if v not in PROCEDURE_KINDS:
            raise ValueError(f"unknown case_kind {v}")
        return v


class MinorAddon(BaseModel):
    after_count: int = Field(ge=0, le=20)
    blocked_at: int = Field(ge=1, le=20)


class FacilityCapacity(BaseModel):
    kind: str                                  # robotic | mix_exclusive | fixed_slots
    options: list[CapacityOption] = []
    exclusive: bool = True
    minor_addon: Optional[MinorAddon] = None
    slot_times: Optional[list[str]] = None     # fixed_slots only, "HH:MM"
    case_minutes: Optional[int] = Field(default=None, ge=15, le=480)

    @field_validator("kind")
    @classmethod
    def known_capacity_kind(cls, v):
        if v not in ("robotic", "mix_exclusive", "fixed_slots"):
            raise ValueError(f"unknown capacity kind {v}")
        return v

    @field_validator("slot_times")
    @classmethod
    def valid_slot_times(cls, v):
        if v is None:
            return v
        if len(set(v)) != len(v):
            raise ValueError("slot times must be distinct")
        for t in v:
            if not re.fullmatch(r"\d{2}:\d{2}", t):
                raise ValueError(f"slot time {t!r} must be HH:MM")
        return sorted(v)


class PostOpVisitIn(BaseModel):
    label: str
    offset_days: int = Field(ge=1, le=365)
    mode: str = "office"                       # office | telehealth
    location_locked: bool = False

    @field_validator("mode")
    @classmethod
    def known_mode(cls, v):
        if v not in ("office", "telehealth"):
            raise ValueError("mode must be office or telehealth")
        return v


class PostOpRuleIn(BaseModel):
    match: list[str] = Field(min_length=1)     # keywords, lowercase
    visits: list[PostOpVisitIn] = Field(min_length=1)


class ConfigPayload(BaseModel):
    # pre-existing
    office_full_threshold:     Optional[int] = Field(default=None, ge=1, le=20)
    office_lookahead_days:     Optional[int] = Field(default=None, ge=1, le=60)
    hospital_lookahead_days:   Optional[int] = Field(default=None, ge=1, le=90)
    reminder_lead_days:        Optional[list[int]] = None
    # alerts & windows
    critical_overdue_hours:    Optional[int] = Field(default=None, ge=1, le=720)
    labs_alert_window_days:    Optional[int] = Field(default=None, ge=1, le=60)
    post_op_docs_alert_days:   Optional[int] = Field(default=None, ge=1, le=60)
    unresponsive_after_days:   Optional[int] = Field(default=None, ge=1, le=365)
    preop_valid_days:          Optional[int] = Field(default=None, ge=30, le=730)
    schedule_horizon_days:     Optional[int] = Field(default=None, ge=30, le=730)
    completed_window_days:     Optional[int] = Field(default=None, ge=1, le=365)
    # steps engine
    step_expected_days_hospital: Optional[dict[str, int]] = None
    step_expected_days_office:   Optional[dict[str, int]] = None
    step_titles_hospital:        Optional[dict[str, str]] = None
    step_titles_office:          Optional[dict[str, str]] = None
    # structured
    post_op_schedules:         Optional[list[PostOpRuleIn]] = None
    capacity_rules:            Optional[dict[str, FacilityCapacity]] = None

    @field_validator("step_expected_days_hospital", "step_expected_days_office")
    @classmethod
    def days_in_range(cls, v):
        if v is None:
            return v
        for k, d in v.items():
            if not (1 <= int(d) <= 90):
                raise ValueError(f"expected days for {k} must be 1-90")
        return v
```

In `put_config`, the structured Pydantic values must be stored as plain JSON — change the storage loop to serialize:

```python
    data = payload.model_dump(exclude_unset=True, mode="json")
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_surgery_settings.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/surgery_config.py backend/tests/test_surgery_settings.py
git commit -m "feat(surgery-settings): validated config payloads for all new keys"
```

---

### Task 4: Post-op schedules from config

**Files:**
- Modify: `backend/app/services/post_op_schedule.py` (PROCEDURE_RULES + determine_post_op_schedule)
- Test: `backend/tests/test_post_op_schedule_config.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_post_op_schedule_config.py
"""Post-op schedule rules: config-driven with hardcoded fallback parity."""
from types import SimpleNamespace
from app.services.post_op_schedule import (
    determine_post_op_schedule, rules_from_config, DEFAULT_PROCEDURE_RULES,
)
from app.models.surgery_config import SurgeryConfig


def _surgery(desc):
    return SimpleNamespace(procedures=[{"description": desc}])


def test_default_parity_hysterectomy(db_session):
    visits = determine_post_op_schedule(_surgery("Robotic hysterectomy"), db=db_session)
    assert [(v.days_post_op, v.suggested_location) for v in visits] == \
        [(7, "office"), (42, "office")]
    assert visits[1].location_locked is True


def test_config_override_changes_offsets(db_session):
    db_session.add(SurgeryConfig(key="post_op_schedules", value=[
        {"match": ["hysterectomy"], "visits": [
            {"label": "10 days post-op", "offset_days": 10, "mode": "office"}]},
    ]))
    db_session.commit()
    visits = determine_post_op_schedule(_surgery("Robotic hysterectomy"), db=db_session)
    assert [(v.days_post_op,) for v in visits] == [(10,)]


def test_no_db_falls_back_to_defaults():
    visits = determine_post_op_schedule(_surgery("LEEP"), db=None)
    assert [(v.days_post_op, v.location_locked) for v in visits] == [(14, True)]
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_post_op_schedule_config.py -v`
Expected: FAIL (`rules_from_config` / `db=` kwarg don't exist).

- [ ] **Step 3: Implement**

In `post_op_schedule.py`:

1. Rename `PROCEDURE_RULES` → `DEFAULT_PROCEDURE_RULES` (keep contents byte-identical).
2. Add:

```python
def rules_from_config(db) -> list[tuple[list[str], list[PostOpVisit]]]:
    """Config-driven rules; falls back to DEFAULT_PROCEDURE_RULES when the
    key is unset, db is None, or the stored JSON is malformed."""
    if db is None:
        return DEFAULT_PROCEDURE_RULES
    try:
        from app.services.surgery.settings import cfg
        raw = cfg(db, "post_op_schedules")
        if not raw:
            return DEFAULT_PROCEDURE_RULES
        out = []
        for rule in raw:
            visits = [PostOpVisit(
                label=v["label"],
                days_post_op=int(v["offset_days"]),
                suggested_location=v.get("mode", "office"),
                location_locked=bool(v.get("location_locked", False)),
            ) for v in rule["visits"]]
            out.append(([k.lower() for k in rule["match"]], visits))
        return out
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "bad post_op_schedules config; using defaults", exc_info=True)
        return DEFAULT_PROCEDURE_RULES
```

3. Change `determine_post_op_schedule(s)` → `determine_post_op_schedule(s, db=None)` and use `rules_from_config(db)` in place of the module constant. Keep the laparoscopy-skip and hysteroscopy+IUD special cases exactly as they are (they key off `keywords` content, which still works).
4. `all_required_appts_filled(s)` → `all_required_appts_filled(s, db=None)`, pass `db` through.
5. Update every caller: `grep -rn "determine_post_op_schedule\|all_required_appts_filled" app/` — pass the `db`/session each caller already has (surgery.py serializer line ~530, milestone hook ~593, and any others).

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_post_op_schedule_config.py tests/ -k "post_op or surgery" -q`
Expected: new tests PASS; no regressions.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/post_op_schedule.py backend/tests/test_post_op_schedule_config.py backend/app/routers/surgery.py
git commit -m "feat(surgery-settings): post-op schedules configurable with default parity"
```

---

### Task 5: Capacity rules from config

**Files:**
- Modify: `backend/app/services/surgery/block_schedule.py:196-300` (DURATIONS, OFFICE_SLOT_TIMES_MIN, can_fit)
- Test: `backend/tests/test_capacity_rules_config.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_capacity_rules_config.py
"""can_fit parity with legacy hardcoded rules + config override."""
from datetime import time
from types import SimpleNamespace
from app.services.surgery.block_schedule import can_fit, office_slot_times_min
from app.models.surgery_config import SurgeryConfig


def _day(facility, slots=(), start=time(7, 30), end=time(16, 30)):
    mk = lambda kind, mins: SimpleNamespace(procedure_kind=kind, duration_minutes=mins)
    return SimpleNamespace(facility=facility, start_time=start, end_time=end,
                            slots=[mk(k, m) for k, m in slots])


def test_medstar_three_180s_max(db_session):
    day = _day("medstar", [("robotic_180", 180)] * 3)
    ok, reason = can_fit(db_session, day, "robotic_180")
    assert not ok and "3" in reason


def test_medstar_no_mixing_180_240(db_session):
    day = _day("medstar", [("robotic_240", 240)])
    ok, _ = can_fit(db_session, day, "robotic_180")
    assert not ok


def test_medstar_minor_addon_after_two_robotics(db_session):
    day = _day("medstar", [("robotic_180", 180)] * 2)
    ok, _ = can_fit(db_session, day, "minor")
    assert ok


def test_crmc_six_minors_max(db_session):
    day = _day("crmc", [("minor", 90)] * 6, start=time(8, 0), end=time(18, 0))
    ok, _ = can_fit(db_session, day, "minor")
    assert not ok


def test_crmc_no_mix(db_session):
    day = _day("crmc", [("major", 180)], start=time(8, 0), end=time(16, 0))
    ok, _ = can_fit(db_session, day, "minor")
    assert not ok


def test_office_seven_slots_default(db_session):
    assert len(office_slot_times_min(db_session)) == 7


def test_office_slots_config_override(db_session):
    db_session.add(SurgeryConfig(key="capacity_rules", value={
        "office": {"kind": "fixed_slots",
                    "slot_times": ["08:00", "09:00", "10:00"],
                    "case_minutes": 60}}))
    db_session.commit()
    assert office_slot_times_min(db_session) == [480, 540, 600]
    day = _day("office", [("office", 60)] * 3, start=time(7, 0), end=time(17, 0))
    ok, reason = can_fit(db_session, day, "office")
    assert not ok and "3" in reason


def test_time_window_hard_wall(db_session):
    # 300-minute day can't take a second 180 after one 180
    day = _day("crmc", [("major", 180)], start=time(9, 0), end=time(14, 0))
    ok, reason = can_fit(db_session, day, "major")
    assert not ok and "minutes" in reason
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_capacity_rules_config.py -v`
Expected: FAIL (`office_slot_times_min` doesn't exist; can_fit signature may already take db — confirm at `block_schedule.py:224`).

- [ ] **Step 3: Implement config-driven capacity**

In `block_schedule.py`, after `OFFICE_SLOT_TIMES_MIN`, add the default rules JSON and loaders:

```python
# Default capacity rules — mirror of the previously hardcoded logic.
# Overridable via SurgeryConfig key "capacity_rules" (validated shape in
# surgery_config.FacilityCapacity).
DEFAULT_CAPACITY_RULES = {
    "medstar": {
        "kind": "robotic",
        "options": [{"case_kind": "robotic_180", "max": 3},
                     {"case_kind": "robotic_240", "max": 2}],
        "exclusive": True,
        "minor_addon": {"after_count": 2, "blocked_at": 3},
    },
    "crmc": {
        "kind": "mix_exclusive",
        "options": [{"case_kind": "minor", "max": 6},
                     {"case_kind": "major", "max": 2}],
    },
    "office": {
        "kind": "fixed_slots",
        "slot_times": ["07:30", "08:30", "09:30", "10:30", "11:30",
                        "14:30", "15:30"],
        "case_minutes": 60,
    },
}


def capacity_rules(db) -> dict:
    """Merged capacity rules: config override per facility, else defaults."""
    rules = {k: dict(v) for k, v in DEFAULT_CAPACITY_RULES.items()}
    if db is not None:
        try:
            from app.services.surgery.settings import cfg
            override = cfg(db, "capacity_rules") or {}
            for fac, r in override.items():
                rules[fac] = r
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "bad capacity_rules config; using defaults", exc_info=True)
    return rules


def office_slot_times_min(db) -> list[int]:
    """Office slot start times as minutes-from-midnight (config-driven)."""
    r = capacity_rules(db).get("office") or {}
    times = r.get("slot_times") or DEFAULT_CAPACITY_RULES["office"]["slot_times"]
    out = []
    for t in times:
        h, m = t.split(":")
        out.append(int(h) * 60 + int(m))
    return sorted(out)
```

Rewrite `can_fit` to interpret the rules (keep the signature and the time-window hard wall first, byte-similar error strings so the frontend keeps making sense):

```python
def can_fit(db: Session, block_day: BlockDay, procedure_kind: str) -> tuple[bool, str]:
    existing = list(block_day.slots or [])
    counts: dict[str, int] = {}
    for sl in existing:
        counts[sl.procedure_kind] = counts.get(sl.procedure_kind, 0) + 1

    block_minutes = (
        (block_day.end_time.hour * 60 + block_day.end_time.minute)
        - (block_day.start_time.hour * 60 + block_day.start_time.minute)
    )
    used_minutes = sum(sl.duration_minutes for sl in existing)
    incoming = DURATIONS.get(procedure_kind, 60)
    if used_minutes + incoming > block_minutes:
        return False, (f"Day only has {block_minutes} minutes ({used_minutes} used); "
                        f"a {incoming}-minute case won't fit.")

    rule = capacity_rules(db).get(block_day.facility)
    if rule is None:
        return False, f"Unknown facility: {block_day.facility}"

    kind = rule.get("kind")
    options = {o["case_kind"]: o["max"] for o in rule.get("options", [])}

    if kind == "robotic":
        if procedure_kind in options:
            # exclusivity: no other option-kind may already be booked
            if rule.get("exclusive", True):
                for other in options:
                    if other != procedure_kind and counts.get(other, 0) > 0:
                        return False, (f"Day already has a {DURATIONS.get(other)}-min "
                                        f"robotic; can't add {DURATIONS.get(procedure_kind)}-min.")
            if counts.get(procedure_kind, 0) >= options[procedure_kind]:
                return False, (f"Day already has {options[procedure_kind]} × "
                                f"{DURATIONS.get(procedure_kind)}-min robotics (max).")
            return True, ""
        if procedure_kind == "minor" and rule.get("minor_addon"):
            addon = rule["minor_addon"]
            robotic_counts = {k: counts.get(k, 0) for k in options}
            total = sum(robotic_counts.values())
            if total >= addon["blocked_at"]:
                return False, (f"Day full with {total} robotics — no minor add-ons.")
            if total == addon["after_count"]:
                return True, ""
            return False, (f"Minors at {block_day.facility} require "
                            f"{addon['after_count']} robotics already booked; "
                            f"currently {total}.")
        return False, f"{block_day.facility} block doesn't accept {procedure_kind} cases."

    if kind == "mix_exclusive":
        if procedure_kind not in options:
            return False, f"{block_day.facility} block doesn't accept {procedure_kind} cases."
        for other in options:
            if other != procedure_kind and counts.get(other, 0) > 0:
                return False, (f"Day already has a {other} case; "
                                f"can't mix {procedure_kind}s.")
        if counts.get(procedure_kind, 0) >= options[procedure_kind]:
            return False, (f"Day already has {options[procedure_kind]} "
                            f"{procedure_kind}s (max).")
        return True, ""

    if kind == "fixed_slots":
        if not procedure_kind.startswith("office"):
            return False, f"Office block doesn't accept {procedure_kind} cases."
        max_slots = len(rule.get("slot_times")
                         or DEFAULT_CAPACITY_RULES["office"]["slot_times"])
        if len(existing) >= max_slots:
            return False, f"Day already has {max_slots} office cases (max)."
        return True, ""

    return False, f"Unknown capacity kind for {block_day.facility}: {kind}"
```

Then update every user of `OFFICE_SLOT_TIMES_MIN`: `grep -rn "OFFICE_SLOT_TIMES_MIN" app/` → replace with `office_slot_times_min(db)` (callers have sessions). Keep the constant itself for the default list only if still referenced; otherwise delete it (DEFAULT_CAPACITY_RULES holds the canonical default now).

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_capacity_rules_config.py tests/ -k "block or slot or capacity or book" -q`
Expected: new tests PASS; existing booking tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/surgery/block_schedule.py backend/tests/test_capacity_rules_config.py
git commit -m "feat(surgery-settings): capacity rules and office slot times configurable"
```

---

## Phase B — Steps engine cutover

### Task 6: step_engine.py

**Files:**
- Create: `backend/app/services/surgery/step_engine.py`
- Test: `backend/tests/test_step_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_step_engine.py
"""Steps engine: catalogs, completion, current step, behind-schedule."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from app.services.surgery.step_engine import (
    HOSPITAL_STEPS, OFFICE_STEPS, compute_steps, current_step, is_behind,
    DEFAULT_EXPECTED_DAYS_HOSPITAL,
)


def _hospital_surgery(**over):
    base = dict(
        selected_facility="medstar", chart_number="123", patient_name="X, Y",
        dob=date(1980, 1, 1), cell_phone="1", phone=None, email="a@b.c",
        address_street="s", address_city="c", address_state="MD",
        address_zip="20601", primary_insurance="i", primary_member_id="m",
        surgeon_primary="Dr", procedures=[{"cpt": "58571", "description": "TLH"}],
        diagnoses=[{"icd": "D25.9"}], estimated_minutes=180,
        eligible_facilities=["medstar"], preop_date=date(2026, 6, 1),
        auth_status="approved", clearance_required=False,
        clearance_status=None, assistant_surgeon_required=False,
        assistant_surgeon_name=None, benefits_verified_at=None,
        patient_responsibility=0, amount_paid=0, consent_status=None,
        scheduled_date=None, post_op_appt_date=None, device_required=False,
        device_assigned=False, assistant_surgeon_office_notified_at=None,
        assistant_surgeon_appt_confirmed_at=None, calendar_invite_sent_at=None,
        scheduled_in_modmed_at=None, labs_sent_to_hospital=False,
        post_op_call_status=None, operative_report_status=None,
        payment_posted_to_billing=False, billed_at=None,
        updated_at=datetime(2026, 6, 1), created_at=datetime(2026, 5, 1),
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_catalog_sizes():
    assert len(HOSPITAL_STEPS) == 15
    assert len(OFFICE_STEPS) == 12


def test_every_step_has_default_expected_days():
    assert set(DEFAULT_EXPECTED_DAYS_HOSPITAL) == {st.key for st in HOSPITAL_STEPS}


def test_complete_surgery_info_is_done():
    steps = compute_steps(_hospital_surgery())
    assert steps[0]["key"] == "surgery_info"
    assert steps[0]["state"] == "done"


def test_missing_chart_number_is_todo():
    steps = compute_steps(_hospital_surgery(chart_number=None))
    assert steps[0]["state"] == "todo"


def test_payment_done_when_no_responsibility():
    steps = {s["key"]: s for s in compute_steps(_hospital_surgery())}
    assert steps["payment"]["state"] == "done"


def test_payment_todo_until_paid():
    s = _hospital_surgery(patient_responsibility=500, amount_paid=100)
    steps = {x["key"]: x for x in compute_steps(s)}
    assert steps["payment"]["state"] == "todo"


def test_select_dates_in_progress_with_one_of_two():
    s = _hospital_surgery(scheduled_date=date(2026, 7, 1))
    steps = {x["key"]: x for x in compute_steps(s)}
    assert steps["select_dates"]["state"] == "in_progress"


def test_device_na_unless_required_done_when_assigned():
    s1 = _hospital_surgery()
    assert {x["key"]: x for x in compute_steps(s1)}["device"]["state"] == "n/a"
    s2 = _hospital_surgery(device_required=True, device_assigned=True)
    assert {x["key"]: x for x in compute_steps(s2)}["device"]["state"] == "done"


def test_office_catalog_used_for_office():
    s = _hospital_surgery(selected_facility="office",
                           eligible_facilities=["office"])
    assert len(compute_steps(s)) == 12


def test_current_step_is_first_open_applicable():
    s = _hospital_surgery(benefits_verified_at=None)
    cur = current_step(s)
    assert cur["key"] == "benefits"          # surgery_info done, benefits open


def test_is_behind_uses_expected_days():
    s = _hospital_surgery(updated_at=datetime.now() - timedelta(days=10))
    behind, hrs = is_behind(s, expected_days={"benefits": 3},
                             grace_hours=48)
    assert behind and hrs > 0
    behind2, _ = is_behind(s, expected_days={"benefits": 30}, grace_hours=48)
    assert not behind2
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_step_engine.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement the engine**

```python
# backend/app/services/surgery/step_engine.py
"""Server-side Steps engine — single source of truth for surgery workflow
progress.

Port of the frontend's STEP_CFG_HOSPITAL / STEP_CFG_OFFICE +
stepCompletion() (SurgeryDetail.jsx). Replaces the retired milestone
system for behind-schedule / Critical Alerts. Pure functions over the
Surgery row — no writes.

State values: done | in_progress | todo | n/a
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional


@dataclass(frozen=True)
class StepDef:
    n: int
    key: str
    title: str
    optional: bool = False


HOSPITAL_STEPS: list[StepDef] = [
    StepDef(1,  "surgery_info",     "Surgery Info"),
    StepDef(2,  "benefits",         "Surgery Benefits"),
    StepDef(3,  "payment",          "Payment"),
    StepDef(4,  "consents",         "Consents"),
    StepDef(5,  "select_dates",     "Select Surgery Date & Post-Op Dates"),
    StepDef(6,  "device",           "Allocate Device", optional=True),
    StepDef(7,  "prior_auth",       "Prior Auth", optional=True),
    StepDef(8,  "clearance",        "Clearance / EKG", optional=True),
    StepDef(9,  "asst_surgeon",     "Asst Surgeon / Device Rep", optional=True),
    StepDef(10, "post_to_hospital", "Post Surgery to Hospital"),
    StepDef(11, "modmed_appt",      "Add Surgery Appointment to ModMed"),
    StepDef(12, "labs",             "Labs"),
    StepDef(13, "welfare_fu",       "Post Surgery Welfare F/U"),
    StepDef(14, "notes_reports",    "Surgery Notes & Reports"),
    StepDef(15, "bill",             "Bill Surgery"),
]

OFFICE_STEPS: list[StepDef] = [
    StepDef(1,  "surgery_info", "Add Surgery"),
    StepDef(2,  "benefits",     "Procedure Benefits"),
    StepDef(3,  "payment",      "Payment"),
    StepDef(4,  "consents",     "Consents"),
    StepDef(5,  "select_dates", "Select Procedure Date & Post-Op Dates"),
    StepDef(6,  "device",       "Allocate Device", optional=True),
    StepDef(7,  "prior_auth",   "Prior Auth", optional=True),
    StepDef(8,  "device_rep",   "Device Rep", optional=True),
    StepDef(9,  "modmed_appt",  "Add Procedure Appointment to ModMed"),
    StepDef(10, "welfare_fu",   "Post Surgery Welfare F/U"),
    StepDef(11, "path_report",  "Procedure Pathology Report", optional=True),
    StepDef(12, "bill",         "Bill Surgery"),
]

# Expected days per step — defaults derived from the closest legacy
# milestone duration (smartsheet_seed catalogs) where one existed.
DEFAULT_EXPECTED_DAYS_HOSPITAL: dict[str, int] = {
    "surgery_info": 3, "benefits": 3, "payment": 5, "consents": 3,
    "select_dates": 14, "device": 3, "prior_auth": 5, "clearance": 5,
    "asst_surgeon": 5, "post_to_hospital": 2, "modmed_appt": 2,
    "labs": 3, "welfare_fu": 3, "notes_reports": 14, "bill": 7,
}
DEFAULT_EXPECTED_DAYS_OFFICE: dict[str, int] = {
    "surgery_info": 3, "benefits": 3, "payment": 5, "consents": 3,
    "select_dates": 14, "device": 3, "prior_auth": 5, "device_rep": 5,
    "modmed_appt": 2, "welfare_fu": 3, "path_report": 14, "bill": 7,
}

# Steps that must be complete pre-op (calendar readiness flags).
PRE_OP_STEP_KEYS_HOSPITAL = {st.key for st in HOSPITAL_STEPS[:12]}
PRE_OP_STEP_KEYS_OFFICE = {st.key for st in OFFICE_STEPS[:9]}

# Step key → Surgery datetime column set when that step completed.
# Used to approximate when the *next* step was entered.
_STEP_DONE_TIMESTAMPS = {
    "benefits": "benefits_verified_at",
    "asst_surgeon": "assistant_surgeon_appt_confirmed_at",
    "device_rep": "assistant_surgeon_appt_confirmed_at",
    "post_to_hospital": "calendar_invite_sent_at",
    "modmed_appt": "scheduled_in_modmed_at",
    "bill": "billed_at",
}


def _is_office(s: Any) -> bool:
    return s.selected_facility == "office"


def steps_for(s: Any) -> list[StepDef]:
    return OFFICE_STEPS if _is_office(s) else HOSPITAL_STEPS


def _as_list(v: Any) -> list:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            v = [v]
    return v or []


def surgery_info_missing(s: Any) -> list[str]:
    """Port of frontend checkSurgeryInfoMissing()."""
    missing: list[str] = []

    def need(cond, label):
        if not cond:
            missing.append(label)

    need(s.chart_number, "Chart number")
    need(s.patient_name, "Patient name")
    need(s.dob, "Date of birth")
    need(getattr(s, "cell_phone", None) or s.phone, "Phone")
    need(s.email, "Email")
    need(s.address_street, "Street address")
    need(s.address_city, "City")
    need(s.address_state, "State")
    need(s.address_zip, "ZIP code")
    need(s.primary_insurance, "Primary insurance")
    need(s.primary_member_id, "Primary member ID")
    need(s.surgeon_primary, "Surgeon")
    procs = _as_list(s.procedures)
    need(any((isinstance(p, dict) and (p.get("cpt") or p.get("description")))
             for p in procs), "At least one procedure (CPT)")
    dxs = _as_list(s.diagnoses)
    need(any((isinstance(d, dict) and (d.get("icd") or d.get("description")))
             for d in dxs), "At least one diagnosis (ICD-10)")
    need(s.estimated_minutes and float(s.estimated_minutes) > 0,
         "Estimated minutes")
    need(_as_list(s.eligible_facilities), "Eligible facility")
    need(s.preop_date, "Pre-op date")
    need(s.auth_status, "Prior-auth status decided")
    if getattr(s, "clearance_required", False):
        need(s.clearance_status, "Clearance status decided")
    if getattr(s, "assistant_surgeon_required", False):
        need(s.assistant_surgeon_name, "Assistant surgeon name")
    return missing


def _state(s: Any, key: str) -> str:
    """Completion state for one step key. Port of stepCompletion() /
    stepCompletionOffice() with one backend improvement: the device step
    reads the real device_required/device_assigned columns instead of
    being permanently 'todo'."""
    office = _is_office(s)

    if key == "surgery_info":
        return "done" if not surgery_info_missing(s) else "todo"
    if key == "benefits":
        return "done" if s.benefits_verified_at else "todo"
    if key == "payment":
        resp = float(s.patient_responsibility or 0)
        paid = float(s.amount_paid or 0)
        if resp <= 0:
            return "done"
        return "done" if paid >= resp else "todo"
    if key == "consents":
        cs = (s.consent_status or "").lower()
        return "done" if cs in ("signed", "not_required") else "todo"
    if key == "select_dates":
        picked, post = bool(s.scheduled_date), bool(s.post_op_appt_date)
        if picked and post:
            return "done"
        if picked or post:
            return "in_progress"
        return "todo"
    if key == "device":
        if not getattr(s, "device_required", False):
            return "n/a"
        return "done" if getattr(s, "device_assigned", False) else "todo"
    if key == "prior_auth":
        status = (s.auth_status or "").lower()
        if status == "not_required":
            return "n/a"
        return "done" if status in ("approved", "completed") else "todo"
    if key == "clearance":
        cs = (s.clearance_status or "").lower()
        if cs == "not_required" or not getattr(s, "clearance_required", False):
            return "n/a"
        return ("done" if cs in ("received", "sent_to_hospital", "completed")
                else "todo")
    if key in ("asst_surgeon", "device_rep"):
        if not getattr(s, "assistant_surgeon_required", False):
            return "n/a"
        if (s.assistant_surgeon_office_notified_at
                and s.assistant_surgeon_appt_confirmed_at):
            return "done"
        return "todo"
    if key == "post_to_hospital":
        return "done" if s.calendar_invite_sent_at else "todo"
    if key == "modmed_appt":
        return "done" if s.scheduled_in_modmed_at else "todo"
    if key == "labs":
        return "done" if s.labs_sent_to_hospital else "todo"
    if key == "welfare_fu":
        pocs = (s.post_op_call_status or "").lower()
        return "done" if pocs == "spoke to pt." else "todo"
    if key == "notes_reports":
        ors = (s.operative_report_status or "").lower()
        return "done" if ors in ("completed", "received") else "todo"
    if key == "path_report":
        ors = (s.operative_report_status or "").lower()
        if ors == "not_required":
            return "n/a"
        return "done" if ors in ("completed", "received") else "todo"
    if key == "bill":
        return "done" if s.payment_posted_to_billing else "todo"
    return "todo"


def compute_steps(s: Any, titles: Optional[dict[str, str]] = None) -> list[dict]:
    """Full step list with state — what the serializer emits."""
    titles = titles or {}
    out = []
    for st in steps_for(s):
        out.append({
            "n": st.n,
            "key": st.key,
            "title": titles.get(st.key, st.title),
            "optional": st.optional,
            "state": _state(s, st.key),
        })
    return out


def current_step(s: Any) -> Optional[dict]:
    """First step that is neither done nor n/a."""
    for step in compute_steps(s):
        if step["state"] in ("todo", "in_progress"):
            return step
    return None


def _entered_at(s: Any) -> Optional[datetime]:
    """Approximate when the current step was entered: the latest known
    completion timestamp among done steps, else updated_at/created_at
    (same fallback the legacy milestone age used)."""
    stamps = []
    states = {st["key"]: st["state"] for st in compute_steps(s)}
    for key, field in _STEP_DONE_TIMESTAMPS.items():
        if states.get(key) == "done":
            v = getattr(s, field, None)
            if v:
                stamps.append(v)
    if stamps:
        return max(stamps)
    return s.updated_at or s.created_at


def is_behind(s: Any, *, expected_days: Optional[dict[str, int]] = None,
              grace_hours: int = 48) -> tuple[bool, int]:
    """(is_behind, hours_overdue) for the surgery's current step.
    expected_days: config map {step_key: days}; missing keys use catalog
    defaults. grace_hours folds in the legacy 48h grace."""
    cur = current_step(s)
    if cur is None:
        return False, 0
    defaults = (DEFAULT_EXPECTED_DAYS_OFFICE if _is_office(s)
                else DEFAULT_EXPECTED_DAYS_HOSPITAL)
    exp = (expected_days or {}).get(cur["key"], defaults.get(cur["key"], 7))
    base = _entered_at(s)
    if not base:
        return False, 0
    base_date = base.date() if hasattr(base, "date") else base
    age_days = max(0, (date.today() - base_date).days)
    overdue_days = age_days - int(exp)
    if overdue_days <= 0:
        return False, 0
    overdue_hours = overdue_days * 24
    return overdue_hours > grace_hours, overdue_hours


def expected_days_map(db, s: Any) -> dict[str, int]:
    """Config-driven expected-days map for this surgery's pathway."""
    from app.services.surgery.settings import cfg
    key = ("step_expected_days_office" if _is_office(s)
           else "step_expected_days_hospital")
    defaults = (DEFAULT_EXPECTED_DAYS_OFFICE if _is_office(s)
                else DEFAULT_EXPECTED_DAYS_HOSPITAL)
    override = cfg(db, key) or {}
    return {**defaults, **{k: int(v) for k, v in override.items()}}


def titles_map(db, s: Any) -> dict[str, str]:
    from app.services.surgery.settings import cfg
    key = "step_titles_office" if _is_office(s) else "step_titles_hospital"
    return cfg(db, key) or {}
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_step_engine.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/surgery/step_engine.py backend/tests/test_step_engine.py
git commit -m "feat(steps): server-side steps engine ported from frontend stepCompletion"
```

---

### Task 7: Cut dashboard + serializer over to steps

**Files:**
- Modify: `backend/app/routers/surgery.py` — `_current_milestone` (~73), `_milestone_age_days` (~95), `_is_behind` (~106), `_surgery_dict` (~140, ~293-294), stuck list (~350-370), readiness (~626-658), milestone filter endpoint (~809), `include_milestones` serialization
- Test: `backend/tests/test_steps_cutover.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_steps_cutover.py
"""Dashboard behind-schedule runs on steps — new surgeries are alertable."""
from datetime import datetime, timedelta


def test_new_surgery_without_milestones_can_be_behind(db_session):
    """The whole point of the cutover: a surgery with zero milestone rows
    appears behind-schedule when its current step is old."""
    from app.routers.surgery import _is_behind_steps
    from tests.test_step_engine import _hospital_surgery
    s = _hospital_surgery(benefits_verified_at=None,
                           updated_at=datetime.now() - timedelta(days=30))
    behind, hours = _is_behind_steps(db_session, s)
    assert behind and hours > 0


def test_serializer_emits_steps(db_session, make_surgery):
    """_surgery_dict includes the steps array with current_step fields.
    make_surgery: use the project's existing surgery factory fixture if
    one exists (grep tests/ for Surgery( creation helpers); otherwise
    create a Surgery row inline with minimal fields."""
    from app.routers.surgery import _surgery_dict
    s = make_surgery()
    d = _surgery_dict(s, db=db_session)
    assert isinstance(d["steps"], list) and len(d["steps"]) in (12, 15)
    assert "current_step" in d and "current_step_title" in d
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_steps_cutover.py -v`
Expected: FAIL (`_is_behind_steps` missing).

- [ ] **Step 3: Implement the cutover in surgery.py**

1. Add next to the old helpers:

```python
from app.services.surgery import step_engine


def _is_behind_steps(db: Session, s: Surgery) -> tuple[bool, int]:
    return step_engine.is_behind(
        s,
        expected_days=step_engine.expected_days_map(db, s),
        grace_hours=cfg(db, "critical_overdue_hours"),
    )
```

2. `_surgery_dict`: add a `db` parameter (`def _surgery_dict(s, *, db, include_milestones=False, today=None)`), replace the `_is_behind(s)` / `_current_milestone(s)` calls:

```python
    behind, hours_overdue = _is_behind_steps(db, s)
    cur_step = step_engine.current_step(s)
```

   and replace the two output keys at ~293-294:

```python
        "current_step": cur_step["key"] if cur_step else None,
        "current_step_title": cur_step["title"] if cur_step else None,
        # kept for one release so older frontend code doesn't break:
        "current_milestone": cur_step["key"] if cur_step else None,
        "current_milestone_title": cur_step["title"] if cur_step else None,
        "steps": step_engine.compute_steps(s, titles=step_engine.titles_map(db, s)),
```

   Update ALL `_surgery_dict(` callers to pass `db=db` (grep: `grep -n "_surgery_dict(" app/routers/surgery.py` — there will be many; every endpoint has `db`).
3. Stuck list (~350-370): replace `cur_m = _current_milestone(s)` and `"milestone": cur_m.title ...` with `cur_step = step_engine.current_step(s)` / `"milestone": cur_step["title"] if cur_step else None` (keep the JSON field name `milestone` — the frontend Critical Alerts card reads it; rename in a later UI pass if desired).
4. Readiness function (~626-658): rewrite using steps:

```python
def _preop_readiness(db: Session, s: Surgery) -> tuple[str, list[str], int]:
    """green — all pre-op steps done/n/a; yellow — open pre-op steps;
    red — current step overdue past the configured grace."""
    pre_keys = (step_engine.PRE_OP_STEP_KEYS_OFFICE
                if s.selected_facility == "office"
                else step_engine.PRE_OP_STEP_KEYS_HOSPITAL)
    open_titles = [st["title"] for st in step_engine.compute_steps(s)
                   if st["key"] in pre_keys
                   and st["state"] in ("todo", "in_progress")]
    if not open_titles:
        return "green", [], 0
    behind, _hrs = _is_behind_steps(db, s)
    return ("red", open_titles, 1) if behind else ("yellow", open_titles, 0)
```

   Match the old function's exact name and call sites (read ~620-680 first; preserve its return contract).
5. Milestone-filter endpoint (~809, `_current_milestone(s).kind == milestone`): switch to `step_engine.current_step(s)` and compare against `["key"]`.
6. Delete `_current_milestone`, `_milestone_age_days`, `_is_behind` once `grep -n "_current_milestone\|_milestone_age_days\|_is_behind(" app/` shows no remaining callers.
7. `include_milestones` serialization: where `_surgery_dict` serialized `s.milestones`, drop that block and the parameter; fix callers.

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_steps_cutover.py tests/ -k surgery -q`
Expected: new tests PASS. Existing tests that asserted milestone fields will fail — update them to the steps contract (they are asserting retired behavior, not catching regressions). List each updated test in the commit message.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/surgery.py backend/tests/
git commit -m "feat(steps): dashboard alerts, readiness, and serializer cut over to steps engine"
```

---

### Task 8: Retire milestone writes and auto-advance hooks

**Files:**
- Modify: `backend/app/services/surgery/smartsheet_seed.py:687` (milestone creation), klara/status mapping lines 39, 289, 293, 323
- Modify: any milestone auto-advance hooks (book_slot patient_picks_date advance)
- Modify: `backend/app/services/boldsign_envelopes.py` (imports SurgeryMilestone)

- [ ] **Step 1: Find every milestone write**

Run: `grep -rn "SurgeryMilestone\|milestone" app/ --include="*.py" -l`
Then inspect each hit. Expected writers: `smartsheet_seed.py` (creation), the `book_slot` flow (auto-advance "patient_picks_date"), `boldsign_envelopes.py` (consent milestone advance), possibly `docusign_envelopes.py` (removed in Task 17 anyway).

- [ ] **Step 2: Remove writes**

- `smartsheet_seed.py`: delete the `db.add(SurgeryMilestone(...))` block (~687) and the `HOSPITAL_MILESTONES` / `OFFICE_MILESTONES` catalogs IF nothing else imports them after Task 7 (`grep -rn "HOSPITAL_MILESTONES\|OFFICE_MILESTONES" app/`). Keep the rest of the import logic intact. Delete the `klara_scheduling` status-mapping branches (lines ~39, ~289, ~293, ~323) — read each in context first; remove only the milestone/klara-specific branches, not surrounding status mapping.
- Auto-advance hooks: delete the milestone-advance block, not the surrounding booking logic.
- `boldsign_envelopes.py`: remove the `SurgeryMilestone` import and any milestone-advance block (consent completion already drives the `consents` step via `consent_status`).
- Keep the model class `SurgeryMilestone` and the table (dormant history per spec). Add to its docstring: `RETIRED 2026-06: steps engine (step_engine.py) replaced milestones; table kept as audit history. No reads or writes remain.`

- [ ] **Step 3: Verify zero remaining read/write references**

Run: `grep -rn "SurgeryMilestone\|s.milestones\|\.milestones" app/ --include="*.py" | grep -v "models/surgery.py" | grep -v admin_cleanup`
Expected: no hits (admin_cleanup keeps its inert skip-retired-milestones endpoint per spec).

- [ ] **Step 4: Run full backend suite**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: green (except pre-existing failures noted at baseline).

- [ ] **Step 5: Commit**

```bash
git add backend/app
git commit -m "refactor(steps): retire milestone writes; surgery_milestones now dormant history"
```

---

### Task 9: Frontend consumes API steps

**Files:**
- Modify: `frontend/src/pages/SurgeryDetail.jsx:2943-3186` (STEP_CFG_*, stepCompletion*, checkSurgeryInfoMissing), `stepsFor` callers (~3279-3395), `SurgeryStepTimeline` (~3186)

- [ ] **Step 1: Replace local computation with API data**

The serializer now returns `surgery.steps` (array of `{n, key, title, optional, state}`). In `SurgeryDetail.jsx`:

1. Replace `stepsFor(s)` with `const steps = surgery.steps || []`.
2. Replace `stepCompletion(st.n, surgery, byKind)` calls with `st.state` directly (`SurgeryStepTimeline` and the step-card render functions ~3289-3395 — each `{/* Step N */}` card receives its state via `steps.find(x => x.n === N)?.state` or by passing the step object down).
3. Keep `checkSurgeryInfoMissing` — the `SurgeryInfoChecklist` component still renders the missing-field list client-side from live form state (better UX while editing). Delete `STEP_CFG_HOSPITAL`, `STEP_CFG_OFFICE`, `stepCompletion`, `stepCompletionOffice` once unreferenced.
4. The step-card layout (tones, ordering) keys off `step.key` now, not hardcoded numbers — add a `TONE_BY_KEY` map preserving the existing tone values from the deleted STEP_CFG arrays:

```jsx
const TONE_BY_KEY = {
  surgery_info: 'slate', benefits: 'emerald', payment: 'emerald',
  consents: 'amber', select_dates: 'sky', device: 'teal',
  prior_auth: 'amber', clearance: 'amber', asst_surgeon: 'amber',
  device_rep: 'amber', post_to_hospital: 'sky', modmed_appt: 'sky',
  labs: 'amber', welfare_fu: 'slate', notes_reports: 'slate',
  path_report: 'slate', bill: 'slate',
}
```

- [ ] **Step 2: Build check**

Run: `cd frontend && npm run build`
Expected: clean build, no unused-variable warnings about deleted functions.

- [ ] **Step 3: Manual smoke (local)**

Run backend + frontend dev servers; open a surgery detail page; confirm the timeline renders with the same states as before, "N of M complete" counts match, and an office surgery shows 12 steps.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/SurgeryDetail.jsx
git commit -m "refactor(steps): SurgeryDetail consumes server-computed steps"
```

---

## Phase C — Settings UI

### Task 10: SurgerySettings page skeleton + route + gear button

**Files:**
- Create: `frontend/src/pages/SurgerySettings.jsx`
- Modify: `frontend/src/routes.jsx:172` (rules → redirect; add settings route)
- Modify: `frontend/src/pages/Surgery.jsx:239-251` (header buttons)

- [ ] **Step 1: Page skeleton with tabs**

```jsx
// frontend/src/pages/SurgerySettings.jsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../api'           // match the import path used in SurgeryRules.jsx

const TABS = [
  { id: 'alerts',    label: 'Alerts & Windows' },
  { id: 'steps',     label: 'Workflow Steps' },
  { id: 'postop',    label: 'Post-Op Schedules' },
  { id: 'capacity',  label: 'Facilities & Capacity' },
  { id: 'templates', label: 'Templates' },
]

export default function SurgerySettings() {
  const [tab, setTab] = useState('alerts')
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="page-title mb-4">Surgery Settings</h1>
      <div className="flex gap-1 border-b border-border-subtle mb-6">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
                  className={`px-3 py-2 text-[13px] border-b-2 -mb-px ${
                    tab === t.id
                      ? 'border-plum-600 text-plum-700 font-medium'
                      : 'border-transparent text-muted hover:text-plum-700'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'alerts'    && <AlertsTab />}
      {tab === 'steps'     && <StepsTab />}
      {tab === 'postop'    && <PostOpTab />}
      {tab === 'capacity'  && <CapacityTab />}
      {tab === 'templates' && <TemplatesTab />}
    </div>
  )
}

function Placeholder({ name }) {
  return <div className="text-muted text-sm">{name} — coming in this release.</div>
}
function AlertsTab()    { return <Placeholder name="Alerts & Windows" /> }
function StepsTab()     { return <Placeholder name="Workflow Steps" /> }
function PostOpTab()    { return <Placeholder name="Post-Op Schedules" /> }
function CapacityTab()  { return <Placeholder name="Facilities & Capacity" /> }
function TemplatesTab() { return <Placeholder name="Templates" /> }
```

(The Placeholder components are scaffolding deleted by Tasks 11-15 — each later task replaces one. Match the page chrome — container classes, page-title — to whatever `SurgeryRules.jsx` uses; read its outer return first.)

- [ ] **Step 2: Route + redirect**

In `routes.jsx`, after line 172:

```jsx
  { path: '/surgery/settings',       element: <SurgerySettings />,     module: M.SURGERY, tier: TIER.MANAGE },
```

and change the rules route to a redirect:

```jsx
  { path: '/surgery/rules',          element: <Navigate to="/surgery/settings" replace />, module: M.SURGERY, tier: TIER.MANAGE },
```

(Import `Navigate` from react-router-dom and `SurgerySettings`. Keep `SurgeryRules.jsx` on disk until Task 15 finishes moving its components.)

- [ ] **Step 3: Gear button on Surgery dashboard**

In `Surgery.jsx:239-242`, replace the Rules link with a Settings link (Settings icon is already imported at line 6):

```jsx
          <Link to="/surgery/settings"
                className="btn-ghost text-xs inline-flex items-center gap-1"
                title="Surgery Settings — alerts, steps, capacity, templates">
            <Settings size={13} /> Settings
          </Link>
```

(Keep the Block Schedule button at ~250 as-is; rename ITS icon usage if it also used `Settings` — check and pick a different icon for block schedule, e.g. `CalendarRange`, to avoid two gears.)

- [ ] **Step 4: Build + commit**

Run: `cd frontend && npm run build` — expected clean.

```bash
git add frontend/src/pages/SurgerySettings.jsx frontend/src/routes.jsx frontend/src/pages/Surgery.jsx
git commit -m "feat(surgery-settings): settings page skeleton, route, dashboard gear button"
```

---

### Task 11: Alerts & Windows tab

**Files:**
- Modify: `frontend/src/pages/SurgerySettings.jsx` (replace AlertsTab)
- Move from: `frontend/src/pages/SurgeryRules.jsx:424-545` (config form + AlertRecipients sections)

- [ ] **Step 1: Implement AlertsTab**

Replace the AlertsTab placeholder with a real editor. Reuse the existing pattern from `SurgeryRules.jsx:424-470` (read it first — it already binds `/surgery/config` GET/PUT with react-query):

```jsx
const ALERT_FIELDS = [
  { key: 'critical_overdue_hours',  label: 'Critical Overdue Threshold (Hours)',
    hint: 'A stuck step turns red on the dashboard after this many hours late.' },
  { key: 'labs_alert_window_days',  label: 'Labs Alert Window (Days)',
    hint: 'Flag hospital surgeries this many days out that lack a lab shipment.' },
  { key: 'post_op_docs_alert_days', label: 'Post-Op Docs Alert (Days)',
    hint: 'Flag surgeries this many days post-op with no operative notes.' },
  { key: 'unresponsive_after_days', label: 'Unresponsive After (Days)',
    hint: 'Mark unresponsive when no date picked this long after pre-op.' },
  { key: 'preop_valid_days',        label: 'Pre-Op Validity (Days)',
    hint: 'Pre-op exams older than this require a repeat.' },
  { key: 'schedule_horizon_days',   label: 'Schedule Horizon (Days)',
    hint: 'How far ahead block days are materialized and offered.' },
  { key: 'completed_window_days',   label: 'Completed Window (Days)',
    hint: 'Dashboard "completed surgeries" metric lookback.' },
  { key: 'office_full_threshold',   label: 'Office Full Threshold (Cases)' },
  { key: 'office_lookahead_days',   label: 'Office Alert Lookahead (Days)' },
  { key: 'hospital_lookahead_days', label: 'Hospital Alert Lookahead (Days)' },
]

function AlertsTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const [draft, setDraft] = useState({})
  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', body).then(r => r.data),
    onSuccess: () => { setDraft({}); qc.invalidateQueries(['surgery-config']) },
  })
  if (!data) return <LoadingState />
  const val = (k) => draft[k] ?? data[k] ?? ''
  return (
    <div className="space-y-6">
      <section className="card p-4">
        <h2 className="font-medium mb-3">Alert Thresholds & Windows</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {ALERT_FIELDS.map(f => (
            <label key={f.key} className="block text-[13px]">
              <span className="font-medium">{f.label}</span>
              <input type="number" className="input mt-1 w-28"
                     value={val(f.key)}
                     onChange={e => setDraft(d => ({ ...d, [f.key]: Number(e.target.value) }))} />
              {f.hint && <p className="text-[11px] text-muted mt-0.5">{f.hint}</p>}
            </label>
          ))}
        </div>
        <button className="btn-primary text-xs mt-4"
                disabled={!Object.keys(draft).length || save.isPending}
                onClick={() => save.mutate(draft)}>
          Save Changes
        </button>
        {save.isError && (
          <p className="text-xs text-red-700 mt-2">
            {save.error?.response?.data?.detail?.[0]?.msg || 'Save failed — check values.'}
          </p>
        )}
      </section>
      <AlertRecipientsSection />
      <ReminderLeadDaysSection />
    </div>
  )
}
```

Move `AlertRecipientsSection` (the component at `SurgeryRules.jsx:474-545`) and the reminder-lead-days editor into SurgerySettings.jsx verbatim (lift the whole component functions; adjust names if they differ — read the file). Use the project's existing `LoadingState` component and `card`/`input`/`btn-primary` classes — confirm class names against SurgeryRules.jsx.

- [ ] **Step 2: Build + manual check**

`npm run build` clean; dev-server check: change Critical Overdue to 72, Save, reload — persists; set it to -1 → inline 422 error shows.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/SurgerySettings.jsx
git commit -m "feat(surgery-settings): Alerts & Windows tab with recipients + reminders"
```

---

### Task 12: Workflow Steps tab

**Files:**
- Modify: `frontend/src/pages/SurgerySettings.jsx` (replace StepsTab)
- Modify: `backend/app/routers/surgery_config.py` (expose step catalogs)

- [ ] **Step 1: Backend — expose catalogs**

Add to `surgery_config.py`:

```python
@router.get("/config/step-catalog")
def step_catalog(current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    from app.services.surgery.step_engine import (
        HOSPITAL_STEPS, OFFICE_STEPS,
        DEFAULT_EXPECTED_DAYS_HOSPITAL, DEFAULT_EXPECTED_DAYS_OFFICE,
    )
    def ser(steps, days):
        return [{"n": st.n, "key": st.key, "title": st.title,
                 "optional": st.optional, "default_days": days[st.key]}
                for st in steps]
    return {"hospital": ser(HOSPITAL_STEPS, DEFAULT_EXPECTED_DAYS_HOSPITAL),
            "office":   ser(OFFICE_STEPS,   DEFAULT_EXPECTED_DAYS_OFFICE)}
```

- [ ] **Step 2: Frontend StepsTab**

```jsx
function StepsTab() {
  const qc = useQueryClient()
  const { data: catalog } = useQuery({
    queryKey: ['step-catalog'],
    queryFn: () => api.get('/surgery/config/step-catalog').then(r => r.data),
  })
  const { data: config } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const [draft, setDraft] = useState({})   // {hospital: {key: days}, office: {...}, titles_hospital: {...}, titles_office: {...}}
  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', body).then(r => r.data),
    onSuccess: () => { setDraft({}); qc.invalidateQueries(['surgery-config']) },
  })
  if (!catalog || !config) return <LoadingState />

  const pathway = (id, label, cfgDaysKey, cfgTitlesKey) => {
    const days = { ...(config[cfgDaysKey] || {}), ...(draft[cfgDaysKey] || {}) }
    const titles = { ...(config[cfgTitlesKey] || {}), ...(draft[cfgTitlesKey] || {}) }
    return (
      <section className="card p-4">
        <h2 className="font-medium mb-1">{label}</h2>
        <p className="text-[11px] text-muted mb-3">
          Expected Days drives the behind-schedule and Critical Alerts logic —
          a surgery is flagged when its current step is older than this.
        </p>
        <table className="w-full text-[13px]">
          <thead><tr className="text-left text-muted">
            <th className="py-1 w-8">#</th><th>Step</th>
            <th className="w-32">Expected Days</th></tr></thead>
          <tbody>
            {catalog[id].map(st => (
              <tr key={st.key} className="border-t border-border-subtle">
                <td className="py-1.5">{st.n}</td>
                <td>
                  <input className="input w-full" value={titles[st.key] ?? st.title}
                         onChange={e => setDraft(d => ({ ...d,
                           [cfgTitlesKey]: { ...(d[cfgTitlesKey] || {}), [st.key]: e.target.value } }))} />
                  {st.optional && <span className="chip-muted ml-2">optional</span>}
                </td>
                <td>
                  <input type="number" min={1} max={90} className="input w-20"
                         value={days[st.key] ?? st.default_days}
                         onChange={e => setDraft(d => ({ ...d,
                           [cfgDaysKey]: { ...(d[cfgDaysKey] || {}), [st.key]: Number(e.target.value) } }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    )
  }

  return (
    <div className="space-y-6">
      {pathway('hospital', 'Hospital Pathway (15 Steps)',
               'step_expected_days_hospital', 'step_titles_hospital')}
      {pathway('office', 'Office Pathway (12 Steps)',
               'step_expected_days_office', 'step_titles_office')}
      <button className="btn-primary text-xs"
              disabled={!Object.keys(draft).length || save.isPending}
              onClick={() => save.mutate(draft)}>
        Save Changes
      </button>
    </div>
  )
}
```

(Adjust `chip-muted` to a real existing chip class — check `grep -o 'chip-[a-z]*' frontend/src/index.css | sort -u`.)

- [ ] **Step 3: Build + manual check + commit**

`npm run build`; dev check: change "Benefits" expected days to 10, Save, confirm `GET /surgery/config` shows it and a stale surgery's red/yellow state shifts accordingly.

```bash
git add frontend/src/pages/SurgerySettings.jsx backend/app/routers/surgery_config.py
git commit -m "feat(surgery-settings): Workflow Steps tab — expected days + titles"
```

---

### Task 13: Post-Op Schedules tab

**Files:**
- Modify: `frontend/src/pages/SurgerySettings.jsx` (replace PostOpTab)
- Modify: `backend/app/routers/surgery_config.py` (expose defaults)

- [ ] **Step 1: Backend — expose current effective rules**

```python
@router.get("/config/post-op-defaults")
def post_op_defaults(current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    from app.services.post_op_schedule import DEFAULT_PROCEDURE_RULES
    return {"rules": [
        {"match": kws, "visits": [
            {"label": v.label, "offset_days": v.days_post_op,
             "mode": v.suggested_location, "location_locked": v.location_locked}
            for v in visits]}
        for kws, visits in DEFAULT_PROCEDURE_RULES]}
```

- [ ] **Step 2: Frontend PostOpTab**

```jsx
function PostOpTab() {
  const qc = useQueryClient()
  const { data: config } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const { data: defaults } = useQuery({
    queryKey: ['post-op-defaults'],
    queryFn: () => api.get('/surgery/config/post-op-defaults').then(r => r.data),
  })
  const [rules, setRules] = useState(null)
  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', { post_op_schedules: body }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries(['surgery-config']),
  })
  if (!config || !defaults) return <LoadingState />
  const effective = rules ?? config.post_op_schedules ?? defaults.rules

  const upd = (i, fn) => setRules(effective.map((r, j) => j === i ? fn(r) : r))

  return (
    <div className="space-y-4">
      <p className="text-[12px] text-muted">
        First matching rule (top-down) sets a procedure's follow-up visits.
        Keywords match anywhere in the procedure description.
      </p>
      {effective.map((rule, i) => (
        <section key={i} className="card p-4">
          <div className="flex items-center justify-between mb-2">
            <input className="input w-80 font-medium"
                   value={rule.match.join(', ')}
                   onChange={e => upd(i, r => ({ ...r,
                     match: e.target.value.split(',').map(s => s.trim().toLowerCase()).filter(Boolean) }))} />
            <button className="btn-ghost text-xs text-red-700"
                    onClick={() => setRules(effective.filter((_, j) => j !== i))}>
              Remove Rule
            </button>
          </div>
          {rule.visits.map((v, k) => (
            <div key={k} className="flex items-center gap-2 text-[13px] py-1">
              <input className="input w-44" value={v.label}
                     onChange={e => upd(i, r => ({ ...r, visits: r.visits.map((x, m) =>
                       m === k ? { ...x, label: e.target.value } : x) }))} />
              <input type="number" min={1} max={365} className="input w-20" value={v.offset_days}
                     onChange={e => upd(i, r => ({ ...r, visits: r.visits.map((x, m) =>
                       m === k ? { ...x, offset_days: Number(e.target.value) } : x) }))} />
              <span className="text-muted">days after surgery</span>
              <select className="input w-28" value={v.mode}
                      onChange={e => upd(i, r => ({ ...r, visits: r.visits.map((x, m) =>
                        m === k ? { ...x, mode: e.target.value } : x) }))}>
                <option value="office">Office</option>
                <option value="telehealth">Telehealth</option>
              </select>
              <label className="text-[11px] flex items-center gap-1">
                <input type="checkbox" checked={!!v.location_locked}
                       onChange={e => upd(i, r => ({ ...r, visits: r.visits.map((x, m) =>
                         m === k ? { ...x, location_locked: e.target.checked } : x) }))} />
                In-Person Required
              </label>
              <button className="btn-ghost text-xs"
                      onClick={() => upd(i, r => ({ ...r, visits: r.visits.filter((_, m) => m !== k) }))}>
                ✕
              </button>
            </div>
          ))}
          <button className="btn-ghost text-xs mt-1"
                  onClick={() => upd(i, r => ({ ...r, visits: [...r.visits,
                    { label: 'New visit', offset_days: 14, mode: 'office', location_locked: false }] }))}>
            + Add Visit
          </button>
        </section>
      ))}
      <div className="flex gap-2">
        <button className="btn-ghost text-xs"
                onClick={() => setRules([...effective,
                  { match: ['keyword'], visits: [{ label: '2 weeks post-op',
                    offset_days: 14, mode: 'office', location_locked: false }] }])}>
          + Add Rule
        </button>
        <button className="btn-primary text-xs" disabled={!rules || save.isPending}
                onClick={() => save.mutate(rules)}>
          Save Changes
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Build + manual check + commit**

`npm run build`; dev check: edit hysterectomy week-1 to 10 days, save, then open a hysterectomy surgery's post-op picker — offsets reflect the change.

```bash
git add frontend/src/pages/SurgerySettings.jsx backend/app/routers/surgery_config.py
git commit -m "feat(surgery-settings): Post-Op Schedules tab"
```

---

### Task 14: Facilities & Capacity tab

**Files:**
- Modify: `frontend/src/pages/SurgerySettings.jsx` (replace CapacityTab)
- Move from: `frontend/src/pages/SurgeryRules.jsx:547-808` (FacilitiesSection)
- Modify: `backend/app/routers/surgery_config.py` (expose capacity defaults)

- [ ] **Step 1: Backend — expose effective capacity rules**

```python
@router.get("/config/capacity-defaults")
def capacity_defaults(current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    from app.services.surgery.block_schedule import DEFAULT_CAPACITY_RULES, DURATIONS
    return {"defaults": DEFAULT_CAPACITY_RULES, "durations": DURATIONS}
```

- [ ] **Step 2: Frontend CapacityTab**

Move the facilities CRUD component from `SurgeryRules.jsx:547-808` verbatim into SurgerySettings.jsx, then add the capacity editor below it:

```jsx
function CapacityRulesSection() {
  const qc = useQueryClient()
  const { data: config } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const { data: defaults } = useQuery({
    queryKey: ['capacity-defaults'],
    queryFn: () => api.get('/surgery/config/capacity-defaults').then(r => r.data),
  })
  const [draft, setDraft] = useState(null)
  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', { capacity_rules: body }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries(['surgery-config']),
  })
  if (!config || !defaults) return <LoadingState />
  const rules = draft ?? config.capacity_rules ?? defaults.defaults
  const upd = (fac, fn) => setDraft({ ...rules, [fac]: fn(rules[fac]) })

  return (
    <section className="card p-4">
      <h2 className="font-medium mb-1">Daily Capacity Rules</h2>
      <p className="text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 mb-3">
        Changing these affects what the booking system accepts. Values are
        validated against each block day's real time window — a case mix
        that exceeds the day's minutes is still rejected at booking time.
      </p>
      {Object.entries(rules).map(([fac, r]) => (
        <div key={fac} className="border-t border-border-subtle py-3">
          <div className="font-medium text-[13px] uppercase mb-2">{fac}</div>
          {(r.options || []).map((o, i) => (
            <div key={o.case_kind} className="flex items-center gap-2 text-[13px] py-0.5">
              <span className="w-28">{o.case_kind}</span>
              <span className="text-muted">max</span>
              <input type="number" min={1} max={20} className="input w-16" value={o.max}
                     onChange={e => upd(fac, x => ({ ...x, options: x.options.map((y, j) =>
                       j === i ? { ...y, max: Number(e.target.value) } : y) }))} />
              <span className="text-muted">cases/day
                ({defaults.durations[o.case_kind] || 60} min each)</span>
            </div>
          ))}
          {r.kind === 'fixed_slots' && (
            <div className="text-[13px]">
              <span className="text-muted">Slot start times (HH:MM, comma-separated):</span>
              <input className="input w-full mt-1"
                     value={(r.slot_times || []).join(', ')}
                     onChange={e => upd(fac, x => ({ ...x,
                       slot_times: e.target.value.split(',').map(s => s.trim()).filter(Boolean) }))} />
            </div>
          )}
          {r.minor_addon && (
            <div className="flex items-center gap-2 text-[13px] py-0.5">
              <span className="text-muted">Minor add-on allowed after</span>
              <input type="number" min={0} max={20} className="input w-16"
                     value={r.minor_addon.after_count}
                     onChange={e => upd(fac, x => ({ ...x, minor_addon:
                       { ...x.minor_addon, after_count: Number(e.target.value) } }))} />
              <span className="text-muted">robotics; blocked at</span>
              <input type="number" min={1} max={20} className="input w-16"
                     value={r.minor_addon.blocked_at}
                     onChange={e => upd(fac, x => ({ ...x, minor_addon:
                       { ...x.minor_addon, blocked_at: Number(e.target.value) } }))} />
            </div>
          )}
        </div>
      ))}
      <button className="btn-primary text-xs mt-3" disabled={!draft || save.isPending}
              onClick={() => save.mutate(rules)}>
        Save Capacity Rules
      </button>
      {save.isError && (
        <p className="text-xs text-red-700 mt-2">
          {JSON.stringify(save.error?.response?.data?.detail) || 'Validation failed.'}
        </p>
      )}
    </section>
  )
}

function CapacityTab() {
  return (
    <div className="space-y-6">
      <FacilitiesSection />        {/* moved from SurgeryRules.jsx */}
      <CapacityRulesSection />
    </div>
  )
}
```

- [ ] **Step 3: Build + manual check + commit**

`npm run build`; dev check: set office slot times to 3 entries, save; open the office date-picker — only 3 start times offered; restore.

```bash
git add frontend/src/pages/SurgerySettings.jsx backend/app/routers/surgery_config.py
git commit -m "feat(surgery-settings): Facilities & Capacity tab with guardrailed editor"
```

---

### Task 15: Templates tab + retire SurgeryRules.jsx

**Files:**
- Modify: `frontend/src/pages/SurgerySettings.jsx` (TemplatesTab + How This Works panel)
- Delete: `frontend/src/pages/SurgeryRules.jsx`
- Modify: `frontend/src/routes.jsx` (drop SurgeryRules import)

- [ ] **Step 1: Move remaining sections**

Move from SurgeryRules.jsx into SurgerySettings.jsx verbatim: procedure-templates CRUD (~809-1070), email-templates editor (~1074-1260), SMS-templates editor (~1265-end). Compose:

```jsx
function TemplatesTab() {
  return (
    <div className="space-y-6">
      <ProcedureTemplatesSection />
      <EmailTemplatesSection />
      <SmsTemplatesSection />
    </div>
  )
}
```

- [ ] **Step 2: How This Works panel**

Take the explanatory prose sections of SurgeryRules.jsx (lines ~80-306 — block rules, consents, scheduling docs) and move them into a collapsible panel at the top of SurgerySettings, **rewriting**: remove "Klara messaging" automation claims (Klara drafts are manual paste — keep that wording), remove "DocuSign webhook" references (say BoldSign), remove `klara_scheduling` milestone mentions, change "milestones" → "steps" throughout.

```jsx
function HowThisWorks() {
  const [open, setOpen] = useState(false)
  return (
    <section className="card p-4 mb-6">
      <button onClick={() => setOpen(o => !o)} className="font-medium text-[13px] w-full text-left">
        How Surgery Scheduling Works {open ? '▾' : '▸'}
      </button>
      {open && <div className="mt-3 text-[13px] space-y-2">{/* rewritten prose */}</div>}
    </section>
  )
}
```

- [ ] **Step 3: Delete SurgeryRules.jsx + fix imports**

```bash
rm frontend/src/pages/SurgeryRules.jsx
```

Remove its import from `routes.jsx:92` (route already redirects). Run `grep -rn "SurgeryRules" frontend/src/` — expected: no hits.

- [ ] **Step 4: Build + commit**

`npm run build` clean.

```bash
git add -A frontend/src
git commit -m "feat(surgery-settings): Templates tab; retire SurgeryRules page"
```

---

## Phase D — Dead-code cleanup

### Task 16: klara_scheduling remnants

**Files:**
- Modify: `frontend/src/pages/SurgeryDetail.jsx:3540` (the retired case — line number will have shifted after Task 9; grep for it)
- Modify: `backend/app/routers/surgery.py:77-82, 2692` (stale comments)

- [ ] **Step 1: Delete**

- `grep -n "klara_scheduling" frontend/src backend/app -r` — delete the `case 'klara_scheduling': return null` branch and stale comments. KEEP `klara_drafter.py` and the klara-draft/klara-sent endpoints (active manual workflow).
- Re-check: `grep -rn "klara_scheduling" backend/app frontend/src` → only admin_cleanup's inert endpoint may remain.

- [ ] **Step 2: Test + commit**

`venv/bin/python -m pytest tests/ -q` green; `npm run build` clean.

```bash
git add -A
git commit -m "chore: delete retired klara_scheduling remnants"
```

---

### Task 17: DocuSign removal + LARC repoint + pre-flight endpoint

**Files:**
- Create: pre-flight count endpoint in `backend/app/routers/admin_cleanup.py`
- Delete: `backend/app/routers/docusign.py`, `backend/app/services/docusign_envelopes.py`, `backend/app/services/docusign_client.py`, `backend/tests/test_docusign_email.py`
- Modify: `backend/app/routers/surgery.py:4408, 4422-4490` (docusign-send/sync endpoints + template field), `backend/app/main.py` (router include), `backend/app/config.py` (docusign_* settings), `backend/app/database.py` (template seeding), `backend/app/routers/larc.py:496-520`, `frontend/src/pages/SurgeryDetail.jsx` (~5001-5238), `frontend/src/pages/LarcDeviceTypes.jsx`

- [ ] **Step 1: Pre-flight endpoint (deploy before deleting the webhook)**

Add to `admin_cleanup.py` (mirror the auth pattern of its existing GET endpoints):

```python
@router.get("/docusign-open-count")
def docusign_open_count(db: Session = Depends(get_db),
                         current_user: dict = Depends(require_super_admin)):
    """Pre-flight for DocuSign removal: envelopes still out for signature."""
    from app.models.surgery import SurgeryConsentEnvelope
    n = (db.query(SurgeryConsentEnvelope)
           .filter(SurgeryConsentEnvelope.docusign_envelope_id.isnot(None),
                   SurgeryConsentEnvelope.status.notin_(
                       ("signed", "completed", "declined", "voided")))
           .count())
    return {"open_docusign_envelopes": n}
```

(Match the exact super-admin dependency name used elsewhere in that file.)

Commit, deploy backend (see Task 18 commands), then:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  https://backend-809279713851.us-east4.run.app/api/admin/cleanup/docusign-open-count
```

**GATE: If `open_docusign_envelopes` > 0 — STOP here, keep `docusign.py` (webhook) and `docusign_client.py`, do only steps 2.b-2.f below (send-path removal is still safe: new sends are BoldSign-only), and revisit the webhook after the open envelopes finalize. If 0, proceed fully.**

- [ ] **Step 2: Remove DocuSign code**

a. Webhook: delete `backend/app/routers/docusign.py`; remove its `include_router` in `main.py`.
b. Send/sync: in `surgery.py`, delete `consent_docusign_send` (~4422-4459) and `consent_docusign_sync` (~4461-4490). Keep the serializer's `docusign_envelope_id` / `provider` fields (legacy display). At ~4408 keep emitting `docusign_template_id` only if the consent-template admin UI displays it; otherwise drop the key and its UI cell.
c. Services: `rm backend/app/services/docusign_envelopes.py backend/app/services/docusign_client.py` — first confirm the only importers were the deleted endpoints + larc.py: `grep -rn "docusign_envelopes\|docusign_client" backend/app/`.
d. Config: remove all `docusign_*` fields from `config.py` and the DocuSign template seeding block in `database.py` (`grep -n "docusign" backend/app/config.py backend/app/database.py`).
e. Tests: `rm backend/tests/test_docusign_email.py`.
f. Frontend `SurgeryDetail.jsx` (~5001-5238 pre-Task-9 numbering — grep `docusignSend\|docusign-send`): delete the docusign mutations; replace `const send = provider === 'boldsign' ? boldsignSend : docusignSend` with BoldSign only; for envelopes whose `provider === 'docusign'`, render status read-only with no send/sync buttons.

- [ ] **Step 3: LARC repoint**

In `larc.py:496`, replace the endpoint body to call BoldSign instead (keep route path additionally as `/boldsign-templates`; keep `/docusign-templates` as an alias for one release if LarcDeviceTypes is deployed separately — they deploy together, so a clean rename is fine):

```python
@router.get("/boldsign-templates")
def list_boldsign_templates(current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    """List templates from the BoldSign account for the device-type
    enrollment-template picker."""
    import httpx, os
    api_key = os.environ.get("BOLDSIGN_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "BoldSign not configured")
    r = httpx.get("https://api.boldsign.com/v1/template/list",
                   headers={"X-API-KEY": api_key},
                   params={"PageSize": 50, "Page": 1}, timeout=20)
    r.raise_for_status()
    data = r.json()
    return {"templates": [
        {"id": t.get("documentId"), "name": t.get("messageTitle") or t.get("templateName")}
        for t in data.get("result", [])]}
```

(Check `boldsign_envelopes.py` first — if it already has a template-list helper or base-URL constant, reuse it instead of inlining httpx.) Update `LarcDeviceTypes.jsx`: change `docusign-templates` → `boldsign-templates` and the label "DocuSign enrollment template" → "BoldSign Enrollment Template".

- [ ] **Step 4: Verify + test**

```bash
grep -rni "docusign" backend/app frontend/src --include="*.py" --include="*.jsx" \
  | grep -v "docusign_envelope_id\|docusign_template_id\|provider"
```

Expected: no hits beyond the retained column names/provider display. Run `venv/bin/python -m pytest tests/ -q` and `npm run build`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove DocuSign integration (BoldSign-only); repoint LARC template picker"
```

(Secret Manager `docusign-*` secrets: leave in place this release; delete after one clean deploy cycle per spec.)

---

### Task 18: Deploy + production smoke

- [ ] **Step 1: Deploy backend**

```bash
cd backend
gcloud builds submit --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:latest --project=wwc-solutions
gcloud run deploy backend --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:latest \
  --region=us-east4 --project=wwc-solutions
```

(Confirm the exact Artifact Registry path from the last deploy first: `gcloud run services describe backend --region=us-east4 --project=wwc-solutions --format="value(spec.template.spec.containers[0].image)"`. Never use `--config=cloudbuild.yaml`.)

- [ ] **Step 2: Deploy frontend**

Same pattern for the `frontend` service.

- [ ] **Step 3: Smoke checklist (production)**

1. Surgery dashboard loads; Critical Alerts card populated (step-based entries; counts plausible vs. pre-deploy ~10-surgery prior-auth backlog).
2. Create a test surgery → it appears in behind-schedule logic (set its updated_at old via silent test, or just confirm steps array present on a fresh row) — **this is the cutover's headline fix**.
3. SurgeryDetail timeline renders, step states match pre-deploy for 3 spot-checked surgeries (one office, one imported hospital, one new).
4. `/surgery/settings` loads all 5 tabs; `/surgery/rules` redirects.
5. Change Critical Overdue to 72h, confirm dashboard red set shrinks; revert to 48.
6. Book-slot flow on a MedStar block day still enforces 3×180 (try a 4th — rejected with the same style of message).
7. BoldSign consent send works; a legacy DocuSign envelope renders read-only.
8. LARC device-type page lists BoldSign templates.

- [ ] **Step 4: Update tasks.md + commit**

Record new known-good revisions in `tasks.md` (replacing backend-00302-tb8 / frontend-00232-mn6), commit.

---

## Self-review notes

- **Spec coverage:** registry (T1), scalar threads (T2), validated PUT (T3), post-op (T4), capacity (T5), step engine (T6), cutover (T7), milestone retirement (T8), frontend steps (T9), settings UI (T10-15), klara (T16), DocuSign + LARC + pre-flight gate (T17), deploy/smoke (T18). Rules-page prose rewrite covered in T15 Step 2.
- **Naming consistency:** `cfg`, `SETTINGS_DEFAULTS`, `step_engine.compute_steps/current_step/is_behind/expected_days_map/titles_map`, `_is_behind_steps`, `office_slot_times_min`, `capacity_rules`, `DEFAULT_CAPACITY_RULES`, `DEFAULT_PROCEDURE_RULES`, `rules_from_config` used consistently across tasks.
- **Known judgment calls for the executor:** exact fixture names in conftest.py (verify before writing tests); line numbers drift as tasks land — always grep before editing; `_surgery_dict` caller count is large — mechanical but must be complete.
