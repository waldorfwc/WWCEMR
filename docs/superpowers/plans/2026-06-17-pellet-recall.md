# Pellet Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Pellets → Recall worklist of patients due for re-insertion that opens the same call-workflow detail (patient card, insertion history, caller script, claim, dial, log outcome, attempts, history) as the WWE Recall module — reusing the recall engine, gated by pellet permissions.

**Architecture:** Pellet-due patients (the existing `recall_is_due` rule) are materialized into the existing `RecallEntry`/`recall_call_logs` engine tagged `recall_type="Pellet Re-insertion"`. New `/pellets/recall/*` endpoints (`Module.PELLETS`) list/detail them and DELEGATE claim/dial/outcome to the existing recall handlers (called directly — their `Module.RECALL` `Depends` only fires during routing, so direct calls bypass it). `recalls.py` is not modified.

**Tech Stack:** FastAPI, SQLAlchemy, pytest (backend); React + react-query + Vite (frontend).

**Spec:** `docs/superpowers/specs/2026-06-17-pellet-recall-design.md`

**Conventions:** MM/DD/YYYY, Title Case, `now_utc_naive()` never `datetime.utcnow()`; backend pytest via `./venv/bin/python -m pytest ...` from `backend/`; frontend `npm run build` from `frontend/`.

**Grounding facts (verified):**
- `RecallEntry` (`app/models/recall.py`): `chart_number`, `recall_type`, `source`, `patient_name`, `dob`, `cell_phone`, `primary_phone`, `email`, `primary_insurance`, `recall_due` (Date), `last_visit` (Date), `status` (active|completed|suppressed|...), `attempts`, `last_outcome`, `last_attempt_at`, `claimed_by`, `claimed_until`. UniqueConstraint `(chart_number, recall_type)`. `RecallCallLog` has `recall_entry_id, chart_number, event_type, user_email, occurred_at, outcome, notes, duration_seconds`.
- `app/routers/recalls.py` exports module-level: `_entry_to_dict(e, dob=None)`, `_ensure_claim_available`, `_take_claim`, `_release_claim`, `_taxonomy(db)`, and the handlers `get_recall`, `claim_recall(recall_id, db, current_user)`, `release_recall(...)`, `log_call_attempted(...)`, `dial(...)`, `log_outcome(recall_id, payload, db, current_user)`, plus `OutcomePayload`. All gated `Module.RECALL` via `Depends` (bypassed on direct calls).
- `app/routers/pellet.py` exports `_patient_view_extras(p, today, active_months=..., labs_days=14, mammo_days=365) -> dict` returning `{last_visit_date (str|None), recall_due_date (str|None), recall_is_due (bool), ...}` — relies on `p.visits` being loaded. This is the canonical pellet recall computation (used by the roster's "Recall Due" view).
- `PelletPatient`: `chart_number, patient_name, patient_dob, patient_phone, patient_email, primary_insurance, status (active|...)`, `visits` rel. `PelletVisit`: `scheduled_date, inserted_at, location, provider, status, visit_kind`, `doses` rel → `PelletVisitDose(quantity)` → `PelletDoseType(label)`.
- `cfg(db, key)` + `PELLET_SETTINGS_DEFAULTS` in `app/services/pellet/settings.py`.
- Cron pattern: `app/services/fax_poller.py` `start_scheduler()` registers APScheduler jobs; cross-instance lock via `claim_cron_run` (`app/models/cron_run.py`).
- Pellet permission: `Module.PELLETS`. Pellet nav `app/.../components/pellet/PelletNav.jsx` (`LINKS` array), routes in `frontend/src/routes.jsx`. `Module`/`Tier` from `app.permissions.catalog`; `requires_tier` from `app.permissions.dependencies`; `get_db` from `app.database`.
- Tests: function-scoped empty `db`; super-admin `client`. `RecallEntry`/`RecallCallLog` tables exist (recall models registered).

**Constant:** define `PELLET_RECALL_TYPE = "Pellet Re-insertion"` in `recall_sync.py` and import it where needed.

---

## File Structure
- Create `backend/app/services/pellet/recall_sync.py` — `materialize_pellet_recalls` + `PELLET_RECALL_TYPE`.
- Create `backend/app/routers/pellet_recall.py` — list/detail/sync + delegated action endpoints; register in `app/main.py`.
- Modify `backend/app/services/pellet/settings.py` — `recall_caller_script` default.
- Modify `backend/app/services/fax_poller.py` — daily sync cron.
- Create `frontend/src/pages/PelletRecall.jsx`, `frontend/src/pages/PelletRecallDetail.jsx`; modify `routes.jsx`, `components/pellet/PelletNav.jsx`.
- Tests: `test_pellet_recall_sync.py`, `test_pellet_recall_router.py`, `test_pellet_recall_walkthrough.py`.

---

### Task 1: Caller-script setting + recall sync service

**Files:**
- Modify: `backend/app/services/pellet/settings.py` (add the default)
- Create: `backend/app/services/pellet/recall_sync.py`
- Test: `backend/tests/test_pellet_recall_sync.py`

- [ ] **Step 1: Add the caller-script setting default**

In `backend/app/services/pellet/settings.py`, add to the `PELLET_SETTINGS_DEFAULTS` dict (next to the other string settings):

```python
    "recall_caller_script": (
        "Hi, this is the office of Waldorf Women's Care calling about your hormone "
        "pellet therapy. Our records show it's been about {months} months since your "
        "last insertion and you're due to come in. Would you like to schedule your "
        "re-insertion? We have openings at White Plains, Brandywine, and Arlington."
    ),
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_pellet_recall_sync.py
"""materialize_pellet_recalls: pellet-due patients become RecallEntry rows."""
from datetime import date, datetime, timedelta

from app.models.pellet import PelletPatient, PelletVisit
from app.models.recall import RecallEntry
from app.services.pellet.recall_sync import (materialize_pellet_recalls,
                                             PELLET_RECALL_TYPE)
from app.utils.dt import now_utc_naive


def _due_patient(db, chart):
    p = PelletPatient(chart_number=chart, patient_name=f"Pt {chart}", status="active",
                      patient_phone="3015551234", recall_interval_months=4)
    db.add(p); db.commit(); db.refresh(p)
    # Last insertion 200 days ago, no open visit -> recall is due.
    v = PelletVisit(patient_id=p.id, visit_kind="initial", status="billed",
                    inserted_at=now_utc_naive() - timedelta(days=200))
    db.add(v); db.commit()
    return p


def test_sync_creates_entry_for_due_patient(db):
    p = _due_patient(db, "DUE1")
    out = materialize_pellet_recalls(db)
    assert out["created"] == 1
    e = db.query(RecallEntry).filter(RecallEntry.recall_type == PELLET_RECALL_TYPE).one()
    assert e.chart_number == "DUE1" and e.status == "active"
    assert e.patient_name == "Pt DUE1" and e.cell_phone == "3015551234"
    assert e.recall_due is not None and e.last_visit is not None


def test_sync_is_idempotent_and_preserves_attempts(db):
    _due_patient(db, "DUE2")
    materialize_pellet_recalls(db)
    e = db.query(RecallEntry).filter(RecallEntry.recall_type == PELLET_RECALL_TYPE).one()
    e.attempts = 3; e.last_outcome = "Left voicemail"; db.commit()
    out = materialize_pellet_recalls(db)            # second run
    assert out["created"] == 0 and out["updated"] == 1
    db.refresh(e)
    assert e.attempts == 3 and e.last_outcome == "Left voicemail"   # not reset


def test_sync_completes_entry_when_no_longer_due(db):
    p = _due_patient(db, "DUE3")
    materialize_pellet_recalls(db)
    # Patient schedules a future visit -> has an open visit -> no longer due.
    db.add(PelletVisit(patient_id=p.id, visit_kind="repeat", status="new",
                       scheduled_date=date.today() + timedelta(days=10)))
    db.commit()
    out = materialize_pellet_recalls(db)
    assert out["completed"] == 1
    e = db.query(RecallEntry).filter(RecallEntry.recall_type == PELLET_RECALL_TYPE).one()
    assert e.status == "completed"


def test_sync_ignores_wwe_entries(db):
    db.add(RecallEntry(chart_number="WWE1", recall_type="Est - Well-Woman Exam",
                       source="smartsheet", status="active"))
    db.commit()
    materialize_pellet_recalls(db)
    wwe = db.query(RecallEntry).filter(RecallEntry.recall_type == "Est - Well-Woman Exam").one()
    assert wwe.status == "active"     # untouched
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_recall_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.pellet.recall_sync'`.

- [ ] **Step 4: Create the sync service**

```python
# backend/app/services/pellet/recall_sync.py
"""Materialize pellet patients who are due for re-insertion into the shared
recall engine (RecallEntry, recall_type='Pellet Re-insertion'), reusing the
canonical recall_is_due computation. Idempotent; never resets call progress."""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session, joinedload

from app.models.pellet import PelletPatient
from app.models.recall import RecallEntry
from app.utils.dt import now_utc_naive

PELLET_RECALL_TYPE = "Pellet Re-insertion"


def _to_date(s):
    return date.fromisoformat(s) if s else None


def materialize_pellet_recalls(db: Session) -> dict:
    """Upsert a RecallEntry for each active, recall-due pellet patient; complete
    entries whose patient is no longer due. Suppressed entries are left alone."""
    from app.routers.pellet import _patient_view_extras
    today = now_utc_naive().date()
    existing = {e.chart_number: e for e in
                db.query(RecallEntry)
                  .filter(RecallEntry.recall_type == PELLET_RECALL_TYPE).all()}
    seen: set = set()
    created = updated = completed = 0

    patients = (db.query(PelletPatient)
                  .filter(PelletPatient.status == "active")
                  .options(joinedload(PelletPatient.visits)).all())
    for p in patients:
        x = _patient_view_extras(p, today)
        if not x.get("recall_is_due"):
            continue
        seen.add(p.chart_number)
        e = existing.get(p.chart_number)
        if e is not None and e.status == "suppressed":
            continue                      # declined / do-not-call — leave it
        if e is None:
            e = RecallEntry(chart_number=p.chart_number, recall_type=PELLET_RECALL_TYPE,
                            source="pellet", status="active")
            db.add(e); created += 1
        else:
            if e.status != "active":
                e.status = "active"       # re-open if due again
            updated += 1
        e.patient_name = p.patient_name
        e.dob = p.patient_dob
        e.cell_phone = p.patient_phone
        e.email = p.patient_email
        e.primary_insurance = p.primary_insurance
        e.recall_due = _to_date(x.get("recall_due_date"))
        e.last_visit = _to_date(x.get("last_visit_date"))

    for chart, e in existing.items():
        if chart not in seen and e.status == "active":
            e.status = "completed"; completed += 1

    db.commit()
    return {"created": created, "updated": updated, "completed": completed}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_recall_sync.py -v`
Expected: PASS.

If `_patient_view_extras` or a model field differs, report it (and adapt the test seed minimally for required NOT NULL columns).

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/services/pellet/settings.py app/services/pellet/recall_sync.py tests/test_pellet_recall_sync.py
git commit -m "feat(pellet-recall): caller-script setting + recall sync service"
```

---

### Task 2: Pellet recall router — sync, list, detail

**Files:**
- Create: `backend/app/routers/pellet_recall.py`
- Modify: `backend/app/main.py` (import + include_router)
- Test: `backend/tests/test_pellet_recall_router.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_recall_router.py
"""Pellet recall endpoints. `client` is the super-admin fixture."""
from datetime import timedelta

from app.models.pellet import PelletPatient, PelletVisit
from app.models.recall import RecallEntry
from app.services.pellet.recall_sync import PELLET_RECALL_TYPE
from app.utils.dt import now_utc_naive


def _due(db, chart="DUE1"):
    p = PelletPatient(chart_number=chart, patient_name=f"Pt {chart}", status="active",
                      patient_phone="3015551234", recall_interval_months=4)
    db.add(p); db.commit(); db.refresh(p)
    db.add(PelletVisit(patient_id=p.id, visit_kind="initial", status="billed",
                       location="white_plains", provider="Cooke, Aryian, MD",
                       inserted_at=now_utc_naive() - timedelta(days=200)))
    db.commit()
    return p


def test_sync_then_list(client, db):
    _due(db)
    assert client.post("/api/pellets/recall/sync").status_code == 200
    items = client.get("/api/pellets/recall").json()["items"]
    assert len(items) == 1 and items[0]["chart_number"] == "DUE1"


def test_detail_has_insertion_history_and_script(client, db):
    _due(db, "DUE2")
    client.post("/api/pellets/recall/sync")
    rid = client.get("/api/pellets/recall").json()["items"][0]["id"]
    body = client.get(f"/api/pellets/recall/{rid}").json()
    assert body["recall"]["chart_number"] == "DUE2"
    assert len(body["insertion_history"]) == 1
    assert body["insertion_history"][0]["location"] == "white_plains"
    assert body["caller_script"] and "outcomes" in body
    assert any(h["event_type"] == "detail_viewed" for h in body["history"])


def test_detail_404_for_non_pellet_entry(client, db):
    e = RecallEntry(chart_number="WWE9", recall_type="Est - Well-Woman Exam",
                    source="smartsheet", status="active")
    db.add(e); db.commit(); db.refresh(e)
    assert client.get(f"/api/pellets/recall/{e.id}").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_recall_router.py -v`
Expected: FAIL — 404 / router not mounted.

- [ ] **Step 3: Create the router (sync, list, detail)**

```python
# backend/app/routers/pellet_recall.py
"""Pellet Recall — a worklist of pellet patients due for re-insertion, surfaced
through the shared recall engine and gated by the pellet module. List + detail
are pellet-specific (insertion history); claim/dial/outcome delegate to the
recall handlers (Task 3)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.pellet import PelletPatient
from app.models.recall import RecallCallLog, RecallEntry
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.routers.recalls import _entry_to_dict, _taxonomy
from app.services.pellet.recall_sync import (materialize_pellet_recalls,
                                             PELLET_RECALL_TYPE)
from app.services.pellet.settings import cfg

router = APIRouter(prefix="/pellets/recall", tags=["pellet-recall"])


def _load_pellet_entry(db: Session, recall_id: str) -> RecallEntry:
    e = db.query(RecallEntry).filter(RecallEntry.id == recall_id).first()
    if e is None or e.recall_type != PELLET_RECALL_TYPE:
        raise HTTPException(status_code=404, detail="pellet recall not found")
    return e


@router.post("/sync")
def sync(db: Session = Depends(get_db),
         current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    return materialize_pellet_recalls(db)


@router.get("")
def list_pellet_recalls(
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
):
    q = (db.query(RecallEntry)
           .filter(RecallEntry.recall_type == PELLET_RECALL_TYPE,
                   RecallEntry.status == "active"))
    if search:
        like = f"%{search.strip()}%"
        q = q.filter((RecallEntry.patient_name.ilike(like)) |
                     (RecallEntry.chart_number.ilike(like)))
    rows = q.order_by(desc(RecallEntry.recall_due)).all()
    return {"items": [_entry_to_dict(e) for e in rows]}


def _effective_date(v):
    """The visit's display/sort date: inserted date, else scheduled date."""
    return (v.inserted_at.date() if v.inserted_at else v.scheduled_date) or date.min


def _insertion_history(db: Session, chart_number: str) -> list[dict]:
    p = (db.query(PelletPatient)
           .filter(PelletPatient.chart_number == chart_number)
           .options(joinedload(PelletPatient.visits)).first())
    if p is None:
        return []
    out = []
    for v in sorted(p.visits or [], key=_effective_date, reverse=True):  # newest first
        eff = _effective_date(v)
        doses = "; ".join(f"{d.dose_type.label} ×{d.quantity}"
                          for d in (v.doses or []) if d.dose_type)
        out.append({"date": eff.strftime("%m/%d/%Y") if eff != date.min else None,
                    "location": v.location, "provider": v.provider,
                    "status": v.status, "visit_kind": v.visit_kind, "doses": doses or None})
    return out


@router.get("/{recall_id}")
def get_pellet_recall(recall_id: str, db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    e = _load_pellet_entry(db, recall_id)
    # Log the view event (same as the WWE detail).
    db.add(RecallCallLog(recall_entry_id=e.id, chart_number=e.chart_number,
                         event_type="detail_viewed",
                         user_email=current_user.get("email")))
    db.commit()
    logs = (db.query(RecallCallLog)
              .filter(RecallCallLog.recall_entry_id == e.id)
              .order_by(desc(RecallCallLog.occurred_at)).limit(50).all())
    permanent, cooldown, completed, all_labels = _taxonomy(db)
    return {
        "recall": _entry_to_dict(e, dob=e.dob),
        "insertion_history": _insertion_history(db, e.chart_number),
        "caller_script": cfg(db, "recall_caller_script"),
        "outcomes": list(all_labels),
        "history": [
            {"id": str(l.id), "event_type": l.event_type, "user_email": l.user_email,
             "occurred_at": str(l.occurred_at), "outcome": l.outcome,
             "notes": l.notes, "duration_seconds": l.duration_seconds}
            for l in logs
        ],
    }
```

(Verified: `_taxonomy(db)` returns `(permanent, cooldown, completed, all_labels)` where `all_labels` is the ordered list of outcome labels — so `"outcomes": list(all_labels)` is correct.)

- [ ] **Step 4: Register the router in `main.py`**

Add `pellet_recall` to the `from app.routers import ...` line, and add an include next to the `pellet` includes:

```python
app.include_router(pellet_recall.router, prefix="/api")
```

(Mirror the existing `pellet`/`pellet_reports` include form.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_recall_router.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/routers/pellet_recall.py app/main.py tests/test_pellet_recall_router.py
git commit -m "feat(pellet-recall): sync + list + detail endpoints"
```

---

### Task 3: Pellet recall router — delegated action endpoints

**Files:**
- Modify: `backend/app/routers/pellet_recall.py` (append the action endpoints)
- Test: `backend/tests/test_pellet_recall_router.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_claim_and_outcome_delegate(client, db):
    _due(db, "ACT1")
    client.post("/api/pellets/recall/sync")
    rid = client.get("/api/pellets/recall").json()["items"][0]["id"]
    # Claim
    assert client.post(f"/api/pellets/recall/{rid}/claim").status_code == 200
    # Log an outcome -> bumps attempts + writes a call log via the shared handler.
    r = client.post(f"/api/pellets/recall/{rid}/outcome",
                    json={"outcome": "Left voicemail", "notes": "vm 1"})
    assert r.status_code == 200, r.text
    body = client.get(f"/api/pellets/recall/{rid}").json()
    assert body["recall"]["attempts"] >= 1
    assert any(h["outcome"] == "Left voicemail" for h in body["history"])


def test_action_404_on_non_pellet_entry(client, db):
    from app.models.recall import RecallEntry
    e = RecallEntry(chart_number="WWE8", recall_type="Est - Well-Woman Exam",
                    source="smartsheet", status="active")
    db.add(e); db.commit(); db.refresh(e)
    assert client.post(f"/api/pellets/recall/{e.id}/claim").status_code == 404
    assert client.post(f"/api/pellets/recall/{e.id}/outcome",
                       json={"outcome": "Scheduled"}).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_recall_router.py -k "delegate or non_pellet" -v`
Expected: FAIL — 404/405 on the action routes (not yet defined).

- [ ] **Step 3: Append the delegated action endpoints**

Add to `app/routers/pellet_recall.py` (extend the imports from `recalls` first):

```python
from app.routers.recalls import (OutcomePayload, claim_recall, release_recall,
                                  log_call_attempted, dial, log_outcome)


@router.post("/{recall_id}/claim")
def claim(recall_id: str, db: Session = Depends(get_db),
          current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    _load_pellet_entry(db, recall_id)
    return claim_recall(recall_id, db, current_user)


@router.delete("/{recall_id}/claim", status_code=200)
def release(recall_id: str, db: Session = Depends(get_db),
            current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    _load_pellet_entry(db, recall_id)
    return release_recall(recall_id, db, current_user)


@router.post("/{recall_id}/call-attempted")
def call_attempted(recall_id: str, db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    _load_pellet_entry(db, recall_id)
    return log_call_attempted(recall_id, db, current_user)


@router.post("/{recall_id}/dial")
def dial_pellet(recall_id: str, db: Session = Depends(get_db),
                current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    _load_pellet_entry(db, recall_id)
    return dial(recall_id, db, current_user)


@router.post("/{recall_id}/outcome")
def outcome(recall_id: str, payload: OutcomePayload, db: Session = Depends(get_db),
            current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    _load_pellet_entry(db, recall_id)
    return log_outcome(recall_id, payload, db, current_user)
```

(Verified signatures: `log_outcome(recall_id, payload, db, current_user)`, `claim_recall(recall_id, db, current_user)`, `release_recall(recall_id, db, current_user)`, `log_call_attempted(recall_id, db, current_user)`, `dial(recall_id, db, current_user)` — the calls above match.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_recall_router.py -v`
Expected: PASS (all router tests).

- [ ] **Step 5: Confirm the WWE recall tests are still green (we didn't touch recalls.py)**

Run: `cd backend && ./venv/bin/python -m pytest tests/ -k "recall and not pellet" -q`
Expected: no NEW failures.

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/routers/pellet_recall.py tests/test_pellet_recall_router.py
git commit -m "feat(pellet-recall): claim/dial/outcome endpoints delegating to the recall engine"
```

---

### Task 4: Daily sync cron

**Files:**
- Modify: `backend/app/services/fax_poller.py` (register a daily job)
- Test: `backend/tests/test_pellet_recall_sync.py` (append a cron-callable smoke)

- [ ] **Step 1: Inspect the scheduler + the cron-lock pattern**

Run: `cd backend && grep -n "claim_cron_run\|add_job\|def start_scheduler\|CronTrigger\|def _pellet" app/services/fax_poller.py | head`
Read how an existing pellet/email cron is registered + wrapped with `claim_cron_run` (cross-instance lock).

- [ ] **Step 2: Add the cron job function + registration**

Add a job function near the other pellet crons in `fax_poller.py` and register it in `start_scheduler()` to run daily (e.g. 3 AM), wrapped in the same `claim_cron_run` lock the other crons use. Model it on the existing `_pellet_slot_materialize` cron:

```python
def _pellet_recall_sync():
    """Daily: refresh the pellet recall worklist (idempotent)."""
    from app.database import SessionLocal
    from app.services.pellet.recall_sync import materialize_pellet_recalls
    db = SessionLocal()
    try:
        from app.services.cron_lock import claim_cron_run   # match the existing import path
        if not claim_cron_run(db, "pellet_recall_sync"):
            return
        materialize_pellet_recalls(db)
    finally:
        db.close()
```

Register in `start_scheduler()` alongside the others:

```python
    scheduler.add_job(_pellet_recall_sync, CronTrigger(hour=3, minute=0),
                      id="pellet_recall_sync", replace_existing=True)
```

(Use the EXACT `claim_cron_run` import path and `add_job`/trigger style the existing crons use — read them in Step 1 and mirror precisely. If the existing crons use a different lock helper signature, match it.)

- [ ] **Step 3: Append a smoke test (the job runs without error)**

```python
def test_cron_job_runs(db, monkeypatch):
    # The cron wrapper should call the sync without raising. Patch the lock to
    # allow the run and SessionLocal to use the test session.
    import app.services.fax_poller as fp
    from app.services.pellet import recall_sync
    monkeypatch.setattr("app.services.cron_lock.claim_cron_run", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(fp, "SessionLocal", lambda: db, raising=False)
    # Should not raise.
    fp._pellet_recall_sync()
```

(If `SessionLocal`/`claim_cron_run` are imported inside the function rather than module-level, adapt the monkeypatch targets to the actual import path used in Step 2. The point is: calling `_pellet_recall_sync()` doesn't raise.)

- [ ] **Step 4: Run test**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_recall_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/fax_poller.py tests/test_pellet_recall_sync.py
git commit -m "feat(pellet-recall): daily sync cron (cross-instance locked)"
```

---

### Task 5: Frontend — Recall worklist page + nav + route

**Files:**
- Create: `frontend/src/pages/PelletRecall.jsx`
- Modify: `frontend/src/routes.jsx`, `frontend/src/components/pellet/PelletNav.jsx`

- [ ] **Step 1: Inspect the patterns**

Run from `frontend/`:
- `grep -n "to:\|label:\|LINKS\|TIER" src/components/pellet/PelletNav.jsx | head`
- `grep -n "path: 'reports'\|path: 'activity'\|M.PELLETS" src/routes.jsx | head`
- Skim `src/pages/PelletReports.jsx` for the `api`/`fmt` imports + page shell, and `src/pages/Recalls.jsx` for the WWE recall list/detail layout to mirror.

- [ ] **Step 2: Add the nav link + route**

`PelletNav.jsx` `LINKS` array, after Reports (or near it):
```javascript
    { to: '/pellets/recall',    label: 'Recall',    tier: TIER.WORK },
```
`routes.jsx` — import + child route under `/pellets`:
```javascript
import PelletRecall from './pages/PelletRecall'
```
```javascript
    { path: 'recall',       element: <PelletRecall />,        module: M.PELLETS, tier: TIER.WORK },
```

- [ ] **Step 3: Create `PelletRecall.jsx`**

A worklist page that:
- On mount, `useMutation`/effect calls `POST /pellets/recall/sync` once, then invalidates the list query (so the list reflects a fresh sync). Show a small "Refresh" button that re-runs the sync.
- `useQuery(['pellet-recall-list', search], () => api.get('/pellets/recall', { params: { search: search||undefined } }).then(r => r.data))`.
- Render a table: Patient (name + chart#), Phone, Last Insertion (`fmt.date`), Recall Due (`fmt.date`), Attempts, Last Outcome, claim state (show "🔒 {claimed_by}" when claimed). A search box bound to `search`.
- Clicking a row opens `<PelletRecallDetail recallId={id} onClose={...} />` (Task 6) in a modal/overlay; on close, invalidate the list query (attempts/outcome may have changed).
- Use `fmt.date` (MM/DD/YYYY); Title Case headers; empty state ("No patients due for recall.").

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: succeeds (PelletRecallDetail may be a stub import until Task 6 — if so, create a minimal placeholder component now and flesh it out in Task 6, or implement Task 6 before building).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/pages/PelletRecall.jsx src/routes.jsx src/components/pellet/PelletNav.jsx
git commit -m "feat(pellet-recall): Recall worklist page + nav + route"
```

---

### Task 6: Frontend — Recall detail modal

**Files:**
- Create: `frontend/src/pages/PelletRecallDetail.jsx`

- [ ] **Step 1: Read the reference layout**

Read `src/pages/Recalls.jsx` (the WWE recall detail/"Update Recall" modal) to mirror its structure + styling, and `src/pages/PelletReports.jsx` for the `api`/`fmt`/modal patterns.

- [ ] **Step 2: Create `PelletRecallDetail.jsx`**

A modal component `({ recallId, onClose })` that mirrors the WWE "Update Recall" layout (per the screenshot). It:
- `useQuery(['pellet-recall', recallId], () => api.get(`/pellets/recall/${recallId}`).then(r => r.data))` → `{recall, insertion_history, caller_script, outcomes, history}`.
- On mount, claims the recall: `api.post(`/pellets/recall/${recallId}/claim`)` (best-effort; show a banner if 409 "another user is working this"). Release the claim on close: `api.delete(`/pellets/recall/${recallId}/claim`)`.
- **Header:** "Update Recall" + close (×) → `onClose`.
- **Patient card:** name, "Chart #{chart_number} · DOB {fmt.date(dob)}", a phone chip (`cell_phone||primary_phone`) with a **Dial** button → `api.post(`/pellets/recall/${recallId}/dial`)` (alert the response detail on error, e.g. RingCentral not configured); grid of Last Visit (`fmt.date(recall.last_visit)`), Recall Type ("Pellet Re-insertion"), Recall Due, Attempts, Insurance, Email.
- **Insertion history card:** rows from `insertion_history` — date, location, provider, dosage, status.
- **Caller Script card:** render `caller_script` (collapsible like the WWE one is fine; plain text).
- **Log Call Outcome card:** a `<select>` populated from `outcomes` + a notes `<textarea>` + a **Log Outcome** button → `api.post(`/pellets/recall/${recallId}/outcome`, { outcome, notes })`; on success invalidate `['pellet-recall', recallId]` (refreshes attempts/history) and clear the form.
- **History card:** list `history` newest-first — "{user_email split @} · {event_type/outcome}" + `fmt.dateTime(occurred_at)` + notes.
- Dates MM/DD/YYYY via `fmt`; Title Case titles/buttons. Loading/empty states.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: succeeds, no errors referencing the new files.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/pages/PelletRecallDetail.jsx
git commit -m "feat(pellet-recall): recall detail modal (insertion history, caller script, log outcome)"
```

---

### Task 7: Authenticated walk-through

**Files:**
- Create: `backend/tests/test_pellet_recall_walkthrough.py`

- [ ] **Step 1: Write the walk-through test**

```python
# backend/tests/test_pellet_recall_walkthrough.py
"""Authenticated walk-through of Pellet Recall: an overdue patient is
materialized, listed, opened (insertion history + caller script), and a call
outcome is logged. `client` is the super-admin fixture."""
from datetime import timedelta

from app.models.pellet import PelletPatient, PelletVisit
from app.utils.dt import now_utc_naive


def test_pellet_recall_walkthrough(client, db, capsys):
    log = []
    p = PelletPatient(chart_number="WT-RECALL", patient_name="Roe, Pat", status="active",
                      patient_phone="3015550000", recall_interval_months=4)
    db.add(p); db.commit(); db.refresh(p)
    db.add(PelletVisit(patient_id=p.id, visit_kind="initial", status="billed",
                       location="white_plains", provider="Cooke, Aryian, MD",
                       inserted_at=now_utc_naive() - timedelta(days=210)))
    db.commit()

    # 1. Sync materializes the overdue patient into the recall engine.
    s = client.post("/api/pellets/recall/sync").json()
    assert s["created"] == 1
    log.append(f"1. POST /sync → {s}")

    # 2. The worklist lists them.
    items = client.get("/api/pellets/recall").json()["items"]
    assert len(items) == 1 and items[0]["chart_number"] == "WT-RECALL"
    rid = items[0]["id"]
    log.append(f"2. GET /pellets/recall → 1 due patient ({items[0]['patient_name']})")

    # 3. Detail shows insertion history + caller script + outcomes.
    body = client.get(f"/api/pellets/recall/{rid}").json()
    assert body["insertion_history"][0]["location"] == "white_plains"
    assert body["caller_script"] and body["outcomes"]
    log.append(f"3. GET /pellets/recall/{{id}} → insertion history "
               f"({body['insertion_history'][0]['date']} @ white_plains), caller script, "
               f"{len(body['outcomes'])} outcomes")

    # 4. Log a call outcome (delegates to the recall engine) → attempts bump + history.
    r = client.post(f"/api/pellets/recall/{rid}/outcome",
                    json={"outcome": "Left voicemail", "notes": "left vm"})
    assert r.status_code == 200, r.text
    after = client.get(f"/api/pellets/recall/{rid}").json()
    assert after["recall"]["attempts"] >= 1
    assert any(h["outcome"] == "Left voicemail" for h in after["history"])
    log.append(f"4. POST /outcome 'Left voicemail' → attempts {after['recall']['attempts']}, "
               "logged in history")

    with capsys.disabled():
        print("\n  -- Pellet Recall walk-through (authenticated) --")
        for line in log:
            print("   " + line)
```

- [ ] **Step 2: Run it**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_recall_walkthrough.py -v -s`
Expected: PASS, log prints.

- [ ] **Step 3: Commit**

```bash
cd backend && git add tests/test_pellet_recall_walkthrough.py
git commit -m "test(pellet-recall): authenticated walk-through"
```

---

## Final Verification (after all tasks)
- [ ] `cd backend && ./venv/bin/python -m pytest tests/ -k "pellet_recall" -v` → all PASS.
- [ ] `cd backend && ./venv/bin/python -m pytest tests/ -k "recall and not pellet" -q` → no NEW failures (WWE recalls unaffected — `recalls.py` untouched).
- [ ] `cd frontend && npm run build` → clean.

## Notes for the implementer
- **`recalls.py` is never modified.** The pellet action endpoints import its handlers and call them directly; their `Module.RECALL` `Depends` only fires under FastAPI routing, so a direct call runs the logic without that gate. Every pellet action endpoint first asserts the entry is a `"Pellet Re-insertion"` row (404 otherwise) so a pellet-permissioned user can't act on WWE recalls.
- **Sync is idempotent + non-destructive:** it updates existing pellet entries in place (never resets `attempts`/claim/`last_outcome`), completes entries no longer due, and leaves `suppressed` (declined/DNC) entries alone. It reuses `_patient_view_extras` so the worklist matches the roster's "Recall Due".
- **Recall computation is the existing per-patient rule** (`recall_is_due` via `_patient_view_extras`) — last insertion + `recall_interval_months`×30 days, no open visit.
