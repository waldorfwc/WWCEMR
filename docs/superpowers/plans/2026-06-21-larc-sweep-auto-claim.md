# LARC Sweep Auto-Claim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an automatic LARC reallocation sweep pulls a patient-owned device back to the Owed list, also flip its ownership to `wwc_claimed` (audited), and measure the stale sweep's 180-day window from device receipt.

**Architecture:** Both sweeps funnel through one helper, `_push_to_owed()` in `backend/app/services/larc/sweeps.py`. Add the ownership flip there so both sweeps inherit it. Separately, change `sweep_stale_assignments`'s SQL date filter to `COALESCE(device_received_at, created_at)`. Backend only; no frontend or schema changes.

**Tech Stack:** Python, FastAPI, SQLAlchemy, pytest. Datetimes use `app.utils.dt.now_utc_naive` (never `datetime.utcnow`).

---

## Background for the implementer

- Spec: `docs/superpowers/specs/2026-06-21-larc-sweep-auto-claim-design.md`.
- File under change: `backend/app/services/larc/sweeps.py`.
- `_push_to_owed(db, a, expires_at, actor, summary)` already: dedupes the
  Owed row, returns early if `not a.device`, creates a `LarcOwedPatient`,
  sets `a.is_active = False`, `a.status = "owed"`,
  `a.device.status = "unassigned"`, and logs a `device_reallocated` audit
  event. The sweeps call `db.commit()` once at the end (the helper does
  **not** commit).
- `log_audit(db, *, actor, action, device=None, assignment=None, checkout=None, detail=None, summary=None)`
  is imported at the top of `sweeps.py` already (from
  `app.services.larc.workflow`). It only stages the event; the caller commits.
- Ownership values: `"patient_owned"`, `"wwc_owned"`, `"wwc_claimed"`
  (`LarcDevice.ownership`, default `"wwc_owned"`).
- `LarcAssignment` has `created_at` (DateTime, defaulted), `device_received_at`
  (DateTime, nullable), `inserted_at` (DateTime, nullable).
- `LarcAuditEvent` has `action`, `device_id`, `summary`, `detail` (JSON),
  `occurred_at`.
- Test fixtures: tests take `db` and (for API) `client` fixtures. Device-type
  + device construction pattern is in
  `backend/tests/test_larc_dashboard_ownership.py`:
  ```python
  from app.models.larc import LarcDevice, LarcDeviceType
  def _dt(db):
      dt = LarcDeviceType(name="Mirena", category="larc",
                          default_flow="pharmacy_order", is_active=True)
      db.add(dt); db.commit(); db.refresh(dt)
      return dt
  ```
- Run a single test:
  `cd backend && python -m pytest tests/test_larc_sweep_auto_claim.py::<name> -v`
- All new tests live in a new file: `backend/tests/test_larc_sweep_auto_claim.py`.

---

## File Structure

- **Modify:** `backend/app/services/larc/sweeps.py`
  - `_push_to_owed()` — add the patient_owned → wwc_claimed flip + audit.
  - `sweep_stale_assignments()` — re-base the date filter on
    `COALESCE(device_received_at, created_at)`.
- **Create:** `backend/tests/test_larc_sweep_auto_claim.py` — all new tests.

---

### Task 1: Auto-claim ownership inside `_push_to_owed()`

**Files:**
- Modify: `backend/app/services/larc/sweeps.py` (the `_push_to_owed` function, ~lines 40-69)
- Test: `backend/tests/test_larc_sweep_auto_claim.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_larc_sweep_auto_claim.py` with the following.
These drive both sweeps; the stale-sweep test uses `device_received_at` so it
also passes once Task 2 lands (until then it still exercises the flip because
the default `created_at` is recent — so set `device_received_at` AND keep the
assignment old via `created_at` to be safe). Use a helper that builds a
device + an active, un-inserted assignment.

```python
from datetime import date, timedelta
from app.utils.dt import now_utc_naive
from app.models.larc import (
    LarcAssignment, LarcAuditEvent, LarcDevice, LarcDeviceType, LarcOwedPatient,
)
from app.services.larc.sweeps import (
    sweep_stale_assignments, sweep_expiry_hold,
)


def _dt(db):
    dt = LarcDeviceType(name="Mirena", category="larc",
                        default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _device(db, dt, *, ownership, our_id, expiration_date=None):
    d = LarcDevice(our_id=our_id, device_type_id=dt.id, status="assigned",
                   ownership=ownership, expiration_date=expiration_date)
    db.add(d); db.commit(); db.refresh(d)
    return d


def _assignment(db, dt, d, *, created_days_ago=400, received_days_ago=200):
    a = LarcAssignment(
        chart_number="12345", patient_name="Doe, Jane",
        device_id=d.id, device_type_id=dt.id,
        status="new", is_active=True,
        source_flow="pharmacy_order",
    )
    db.add(a); db.commit(); db.refresh(a)
    # Force the timestamps after insert (created_at has a server default).
    a.created_at = now_utc_naive() - timedelta(days=created_days_ago)
    a.device_received_at = now_utc_naive() - timedelta(days=received_days_ago)
    db.commit(); db.refresh(a)
    return a


def _ownership_events(db, device_id):
    return (db.query(LarcAuditEvent)
              .filter(LarcAuditEvent.device_id == device_id,
                      LarcAuditEvent.action == "ownership_changed")
              .all())


def test_stale_sweep_claims_patient_owned_device(db):
    dt = _dt(db)
    d = _device(db, dt, ownership="patient_owned", our_id="P1")
    _assignment(db, dt, d, created_days_ago=400, received_days_ago=200)

    sweep_stale_assignments(db)

    db.refresh(d)
    assert d.ownership == "wwc_claimed"
    assert d.status == "unassigned"
    owed = db.query(LarcOwedPatient).filter(
        LarcOwedPatient.chart_number == "12345",
        LarcOwedPatient.resolved_at.is_(None)).all()
    assert len(owed) == 1
    assert len(_ownership_events(db, d.id)) == 1


def test_stale_sweep_leaves_wwc_owned_ownership_alone(db):
    dt = _dt(db)
    d = _device(db, dt, ownership="wwc_owned", our_id="W1")
    _assignment(db, dt, d, created_days_ago=400, received_days_ago=200)

    sweep_stale_assignments(db)

    db.refresh(d)
    assert d.ownership == "wwc_owned"          # untouched
    assert d.status == "unassigned"            # still reallocated
    assert _ownership_events(db, d.id) == []   # no ownership_changed event


def test_expiry_sweep_claims_patient_owned_device(db):
    dt = _dt(db)
    # Expires within the 365-day hold window → expiry sweep catches it.
    d = _device(db, dt, ownership="patient_owned", our_id="P2",
                expiration_date=date.today() + timedelta(days=30))
    _assignment(db, dt, d, created_days_ago=10, received_days_ago=5)

    sweep_expiry_hold(db)

    db.refresh(d)
    assert d.ownership == "wwc_claimed"
    assert d.status == "unassigned"
    assert len(_ownership_events(db, d.id)) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_larc_sweep_auto_claim.py -v`
Expected: `test_stale_sweep_claims_patient_owned_device` and
`test_expiry_sweep_claims_patient_owned_device` FAIL on
`assert d.ownership == "wwc_claimed"` (currently stays `"patient_owned"`).
`test_stale_sweep_leaves_wwc_owned_ownership_alone` PASSES already (ownership
isn't touched today) — that's fine, it guards against regression.

- [ ] **Step 3: Implement the ownership flip in `_push_to_owed()`**

In `backend/app/services/larc/sweeps.py`, at the END of `_push_to_owed()`
(after the existing `log_audit(... action="device_reallocated" ...)` call,
before the function returns), add:

```python
    # Auto-claim: a patient-owned device pulled back to the Owed list is
    # now WWC's to bill. (wwc_owned / wwc_claimed are left as-is.)
    if a.device.ownership == "patient_owned":
        a.device.ownership = "wwc_claimed"
        log_audit(db, actor=actor, action="ownership_changed",
                  device=a.device, assignment=a,
                  summary=("Ownership changed: patient owned → wwc claimed. "
                           f"Reason: auto-claimed on reallocation ({actor})."),
                  detail={"from": "patient_owned",
                          "to": "wwc_claimed",
                          "reason": f"auto-claimed on reallocation ({actor})"})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_larc_sweep_auto_claim.py -v`
Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/larc/sweeps.py backend/tests/test_larc_sweep_auto_claim.py
git commit -m "feat(larc): sweeps auto-claim patient-owned devices as WWC Claimed

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Re-base the stale sweep's 180-day clock on device receipt

**Files:**
- Modify: `backend/app/services/larc/sweeps.py` (`sweep_stale_assignments`, the candidate query ~lines 107-113)
- Test: `backend/tests/test_larc_sweep_auto_claim.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_larc_sweep_auto_claim.py`:

```python
def test_stale_sweep_uses_receipt_not_creation_date(db):
    # Created 400 days ago (old) but received only 30 days ago (< 180):
    # must NOT be swept, because the clock runs from receipt.
    dt = _dt(db)
    d = _device(db, dt, ownership="patient_owned", our_id="P3")
    _assignment(db, dt, d, created_days_ago=400, received_days_ago=30)

    sweep_stale_assignments(db)

    db.refresh(d)
    assert d.ownership == "patient_owned"   # not claimed
    assert d.status == "assigned"           # not reallocated


def test_stale_sweep_in_stock_assignment_uses_creation_date(db):
    # No device_received_at (in-stock allocation). Falls back to created_at,
    # which is old → still swept, preserving today's behavior.
    dt = _dt(db)
    d = _device(db, dt, ownership="wwc_owned", our_id="W2")
    a = LarcAssignment(chart_number="67890", patient_name="Roe, Mary",
                       device_id=d.id, device_type_id=dt.id,
                       status="new", is_active=True, source_flow="in_stock")
    db.add(a); db.commit(); db.refresh(a)
    a.created_at = now_utc_naive() - timedelta(days=400)
    a.device_received_at = None
    db.commit()

    sweep_stale_assignments(db)

    db.refresh(d)
    assert d.status == "unassigned"   # still reallocated via created_at fallback
```

- [ ] **Step 2: Run the tests to verify they fail / pass**

Run: `cd backend && python -m pytest tests/test_larc_sweep_auto_claim.py -v`
Expected: `test_stale_sweep_uses_receipt_not_creation_date` FAILS (today the
filter uses `created_at`, so the 400-day-old creation triggers a sweep and the
device gets claimed/reallocated). `test_stale_sweep_in_stock_assignment_uses_creation_date`
PASSES already (created_at filter).

- [ ] **Step 3: Re-base the date filter on `COALESCE(device_received_at, created_at)`**

In `backend/app/services/larc/sweeps.py`:

First ensure `func` is imported. At the top of the file, change the SQLAlchemy
import line:

```python
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
```

(Add the `from sqlalchemy import func` line if it is not already present.)

Then in `sweep_stale_assignments`, replace the candidate query's date filter.
Current:

```python
    candidates = (db.query(LarcAssignment)
                    .options(joinedload(LarcAssignment.device))
                    .filter(LarcAssignment.is_active.is_(True),
                            LarcAssignment.created_at <= cutoff,
                            LarcAssignment.inserted_at.is_(None),
                            LarcAssignment.status.notin_(["billed", "cancelled"]))
                    .all())
```

New:

```python
    candidates = (db.query(LarcAssignment)
                    .options(joinedload(LarcAssignment.device))
                    .filter(LarcAssignment.is_active.is_(True),
                            func.coalesce(LarcAssignment.device_received_at,
                                          LarcAssignment.created_at) <= cutoff,
                            LarcAssignment.inserted_at.is_(None),
                            LarcAssignment.status.notin_(["billed", "cancelled"]))
                    .all())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_larc_sweep_auto_claim.py -v`
Expected: all tests in the file PASS (5 total).

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/larc/sweeps.py backend/tests/test_larc_sweep_auto_claim.py
git commit -m "fix(larc): stale sweep measures 180 days from device receipt

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Regression check — full LARC + sweep suite

**Files:** none (verification only)

- [ ] **Step 1: Run the LARC + workflow test suites**

Run:
```bash
cd backend && python -m pytest tests/ -k "larc or sweep or workflow" -q
```
Expected: all pass (includes the new file plus existing LARC suites).

- [ ] **Step 2: Update the docstring header if needed**

Confirm the module docstring in `sweeps.py` (the numbered list describing the
sweeps) still reads correctly. The stale-sweep bullet currently says
"haven't been inserted within 180 days of creation". Update it to:

```
2. **Reallocate stale assignments** — assignments not inserted within
   180 days of device receipt (falling back to creation date when no
   receipt is recorded) get their device freed; the patient goes on the
   Owed list, and a patient-owned device is auto-claimed as WWC Claimed.
```

- [ ] **Step 3: Commit the docstring fix (if changed)**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/larc/sweeps.py
git commit -m "docs(larc): update stale-sweep docstring for receipt basis + auto-claim

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Auto-claim `patient_owned` → `wwc_claimed` in `_push_to_owed` (both sweeps) → Task 1. ✓
- `wwc_owned` / `wwc_claimed` untouched → Task 1 Step 1 (`test_stale_sweep_leaves_wwc_owned_ownership_alone`). ✓
- `ownership_changed` audit event matching manual endpoint format → Task 1 Step 3. ✓
- `purchasing_patient_*` left untouched → not modified in any task. ✓
- Re-base stale clock on `COALESCE(device_received_at, created_at)` → Task 2. ✓
- In-stock fallback unaffected → Task 2 Step 1 (`test_stale_sweep_in_stock_assignment_uses_creation_date`). ✓
- Behavior matrix (both sweeps × both ownerships) → Tasks 1-2 tests. ✓
- Idempotency (re-running won't re-flip) → guaranteed by the `== "patient_owned"` guard; once flipped the device no longer matches. ✓

**Placeholder scan:** none.

**Type consistency:** ownership string literals (`"patient_owned"`,
`"wwc_owned"`, `"wwc_claimed"`), action `"ownership_changed"`, and field
names (`device_received_at`, `created_at`, `inserted_at`, `ownership`,
`status`) match the models verified in `app/models/larc.py`. `func` import
from `sqlalchemy`. ✓
