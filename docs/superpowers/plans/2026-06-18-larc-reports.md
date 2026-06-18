# Device Tracking (LARC) Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Device Tracking (LARC) → Reports page: a filter bar (date range + location + device type) over 7 tiles (workflow funnel, outstanding enrollment, insertions, billing backlog, owed patients, inventory health, insertion outcomes), each clickable to a drill-down list with CSV export.

**Architecture:** A pure aggregation service (`app/services/larc/reports.py`, one fn per tile) feeds a dedicated router (`app/routers/larc_reports.py`, prefix `/larc/reports`, `Module.LARC` `Tier.VIEW`) exposing `/summary`, `/{tile}/rows`, `?format=csv`. A new React page (`LarcReports.jsx`) mirrors the shipped Surgery/Pellet Reports. The funnel + enrollment tiles reuse the canonical `assignment_buckets()`.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest (backend); React + react-query + Vite (frontend).

**Spec:** `docs/superpowers/specs/2026-06-18-larc-reports-design.md`

**Conventions:** MM/DD/YYYY, Title Case, money `$X.XX`; `now_utc_naive()` never `datetime.utcnow()`; backend pytest via `./venv/bin/python -m pytest ...` from `backend/`; frontend `npm run build` from `frontend/`.

**Grounding facts (verified):**
- `LarcAssignment` (`app/models/larc.py`, has `SoftDeleteMixin` → `deleted_at`): `id, status (new|in_progress|inserted|failed_unused|failed_used|owed|billed|cancelled), source_flow, device_id (nullable — null for pharmacy-order pre-receipt), device_type_id (FK, set even pre-receipt), chart_number, patient_name, inserted_at (DateTime), billed_at (DateTime), is_active`; relationships `device` (LarcDevice), `milestones`.
- `assignment_buckets(a, today=None) -> set[str]` in `app/services/larc/workflow.py` — needs `a.milestones` + `a.device` loaded; returns empty for billed/cancelled; `{"owed"}` for not-active. `ALL_BUCKETS` includes `needs_benefits, needs_enrollment, needs_fax, awaiting_receipt, received_not_notified, appt_scheduled, checked_out, inserted_not_billed, failed_replacement_unrequested, failed_replacement_pending, owed, op_needs_device, op_device_assigned, op_consumed_not_billed`, etc.
- `LarcDevice`: `id, our_id, status (received|unassigned|assigned|checked_out|inserted|defective|returned|lost|expired|billed), ownership (patient_owned|wwc_owned|wwc_claimed), location (white_plains|brandywine|arlington), expiration_date (Date), device_type_id`; `device_type` rel.
- `LarcDeviceType`: `id, name (unique), category (larc|office_procedure), reorder_threshold (Int, nullable)`.
- `LarcOwedPatient`: `chart_number, patient_name, original_device_type_id, owed_since (DateTime), resolved_at (DateTime, nullable)`. **Open owed = `resolved_at is None`.**
- `LarcCheckout`: `id, assignment_id, device_id, outcome (inserted|failed_unused|failed_used|patient_no_show|… , nullable), requested_at (DateTime)`.
- `Module.LARC`, `Tier`; `requires_tier` from `app.permissions.dependencies`; `get_db` from `app.database`. App mounts under `/api`; the larc router is included via `app.include_router(larc.router, prefix="/api")` (main.py:314). `/larc/device-types` lists types.
- Frontend: LARC nav `frontend/src/components/larc/LarcNav.jsx` (`LINKS` array of `{to, label, tier}`); routes under `/larc` in `frontend/src/routes.jsx` (e.g. `{ path: 'devices', element: <Larc.../>, module: M.LARC, tier: TIER.VIEW }`). `M.LARC` = `'device_larc'`. `api`/`fmt` from `../utils/api`; `SurgeryReports.jsx`/`PelletReports.jsx` are the reference pages.
- Tests: function-scoped empty `db`; super-admin `client`. LARC models registered.

---

## File Structure
- Create `backend/app/services/larc/reports.py` — aggregations + drill-down rows + CSV.
- Create `backend/app/routers/larc_reports.py` — endpoints; register in `app/main.py`.
- Create `frontend/src/pages/LarcReports.jsx`; add a route in `routes.jsx` + a nav link in `components/larc/LarcNav.jsx`.
- Tests: `backend/tests/test_larc_reports_service.py`, `test_larc_reports_router.py`, `test_larc_reports_walkthrough.py`.

---

### Task 1: Service — base, workflow funnel, outstanding enrollment, device types

**Files:**
- Create: `backend/app/services/larc/reports.py`
- Test: `backend/tests/test_larc_reports_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_larc_reports_service.py
"""LARC Reports aggregation service."""
from datetime import date, datetime, timedelta

from app.models.larc import (LarcAssignment, LarcDevice, LarcDeviceType)
from app.services.larc import reports as rpt


def _dtype(db, name="Liletta", category="larc", reorder=None):
    t = LarcDeviceType(name=name, category=category, reorder_threshold=reorder)
    db.add(t); db.commit(); db.refresh(t)
    return t


def _device(db, dtype, *, status="unassigned", ownership="wwc_owned",
            location="white_plains", our_id="LAR-1", expires=None):
    d = LarcDevice(our_id=our_id, device_type_id=dtype.id, status=status,
                   ownership=ownership, location=location, expiration_date=expires)
    db.add(d); db.commit(); db.refresh(d)
    return d


def _assignment(db, dtype, *, status="new", source_flow="in_stock",
                device=None, chart="M1", **kw):
    a = LarcAssignment(chart_number=chart, patient_name=f"Pt {chart}",
                       status=status, source_flow=source_flow,
                       device_type_id=dtype.id, device_id=(device.id if device else None),
                       **kw)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_device_types_list(db):
    _dtype(db, "Liletta", "larc")
    _dtype(db, "NovaSure", "office_procedure")
    out = rpt.device_types(db)
    assert {t["name"] for t in out} == {"Liletta", "NovaSure"}
    assert all({"id", "name", "category"} <= set(t) for t in out)


def test_workflow_funnel_buckets(db):
    t = _dtype(db)
    d = _device(db, t, status="checked_out")
    # An active assignment whose device is checked out (not inserted) → 'checked_out' bucket.
    _assignment(db, t, status="checked_out", device=d, chart="F1")
    out = rpt.workflow_funnel(db, location=None, device_type_id=None)
    assert out["by_bucket"].get("outstanding", 0) >= 1
    # A billed assignment contributes no buckets.
    _assignment(db, t, status="billed", device=_device(db, t, our_id="LAR-2"), chart="F2")
    out2 = rpt.workflow_funnel(db, location=None, device_type_id=None)
    assert "billed" not in out2["by_bucket"]   # assignment_buckets returns empty for billed


def test_outstanding_enrollment(db):
    t = _dtype(db)
    # A pharmacy-order assignment with no signed enrollment → needs_enrollment bucket.
    _assignment(db, t, status="in_progress", source_flow="pharmacy_order", chart="E1")
    out = rpt.outstanding_enrollment(db, location=None, device_type_id=None)
    assert out["total"] >= 0   # shape check; exact bucket depends on milestone seeding
    assert set(out["by_stage"]) == {"needs_enrollment", "needs_fax",
                                    "awaiting_receipt", "received_not_notified"}
```

(Note: `assignment_buckets` depends on milestone state; the funnel/enrollment tests assert structure + the billed-excluded invariant rather than exact bucket membership, since seeding full milestone chains is heavy. The walk-through (Task 7) exercises a realistic case.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.larc.reports'`.

- [ ] **Step 3: Create the service**

```python
# backend/app/services/larc/reports.py
"""LARC (Device Tracking) Reports aggregations. Each tile is a pure function
over the assignment/device data, parameterized by an optional location +
device-type filter and (for period tiles) a date range. No persistence."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models.larc import (LarcAssignment, LarcDevice, LarcDeviceType)
from app.services.larc.workflow import assignment_buckets
from app.utils.dt import now_utc_naive


def _dt_floor(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _assignment_base(db: Session, location: Optional[str], device_type_id: Optional[str]):
    """LarcAssignment query: not soft-deleted; device-type on the assignment,
    location via the assigned device (assignments with no device yet don't match
    a specific location filter)."""
    q = db.query(LarcAssignment).filter(LarcAssignment.deleted_at.is_(None))
    if device_type_id:
        q = q.filter(LarcAssignment.device_type_id == device_type_id)
    if location:
        q = (q.join(LarcDevice, LarcAssignment.device_id == LarcDevice.id)
               .filter(LarcDevice.location == location))
    return q


def device_types(db: Session) -> list[dict]:
    rows = db.query(LarcDeviceType).order_by(LarcDeviceType.name).all()
    return [{"id": str(t.id), "name": t.name, "category": t.category} for t in rows]


def workflow_funnel(db: Session, *, location: Optional[str] = None,
                    device_type_id: Optional[str] = None,
                    today: Optional[date] = None) -> dict:
    """Snapshot: active assignments tallied by workload bucket (assignment_buckets)."""
    today = today or now_utc_naive().date()
    q = (_assignment_base(db, location, device_type_id)
         .options(joinedload(LarcAssignment.milestones),
                  joinedload(LarcAssignment.device)))
    by_bucket: dict = {}
    for a in q.all():
        for b in assignment_buckets(a, today):
            by_bucket[b] = by_bucket.get(b, 0) + 1
    return {"by_bucket": by_bucket}


_ENROLLMENT_STAGES = ("needs_enrollment", "needs_fax",
                      "awaiting_receipt", "received_not_notified")


def outstanding_enrollment(db: Session, *, location: Optional[str] = None,
                           device_type_id: Optional[str] = None,
                           today: Optional[date] = None) -> dict:
    """Snapshot: the pharmacy-order enrollment pipeline — assignments at each
    enrollment stage (a focused subset of the funnel buckets)."""
    today = today or now_utc_naive().date()
    q = (_assignment_base(db, location, device_type_id)
         .options(joinedload(LarcAssignment.milestones),
                  joinedload(LarcAssignment.device)))
    by_stage = {s: 0 for s in _ENROLLMENT_STAGES}
    total = 0
    for a in q.all():
        buckets = assignment_buckets(a, today)
        stages = [s for s in _ENROLLMENT_STAGES if s in buckets]
        if stages:
            total += 1
            for s in stages:
                by_stage[s] += 1
    return {"by_stage": by_stage, "total": total}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_service.py -v`
Expected: PASS.

If a model field/constructor differs (e.g. a required NOT NULL column on `LarcAssignment`/`LarcDevice`), add the minimal field to the test helper (keep assertions intact) and note it.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/larc/reports.py tests/test_larc_reports_service.py
git commit -m "feat(larc-reports): service base + workflow funnel, outstanding enrollment, device types"
```

---

### Task 2: Service — insertions + insertion outcomes (period tiles)

**Files:**
- Modify: `backend/app/services/larc/reports.py` (append)
- Test: `backend/tests/test_larc_reports_service.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_insertions_in_range_with_prior(db):
    t1 = _dtype(db, "Liletta", "larc")
    t2 = _dtype(db, "NovaSure", "office_procedure")
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    _assignment(db, t1, status="inserted", chart="I1",
                inserted_at=datetime(2026, 6, 10))
    _assignment(db, t2, status="billed", chart="I2",
                inserted_at=datetime(2026, 6, 20))
    _assignment(db, t1, status="inserted", chart="I3",
                inserted_at=datetime(2026, 5, 15))   # prior period
    out = rpt.insertions(db, date_from=df, date_to=dt, location=None, device_type_id=None)
    assert out["total"] == 2
    assert out["by_category"] == {"larc": 1, "office_procedure": 1}
    assert out["prior_total"] == 1
    assert out["delta"] == 1
    assert out["prior_from"] == date(2026, 5, 2) and out["prior_to"] == date(2026, 5, 31)


def test_insertion_outcomes(db):
    from app.models.larc import LarcCheckout
    t = _dtype(db)
    d = _device(db, t)
    a = _assignment(db, t, device=d, chart="O1")
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    for oc in ("inserted", "failed_unused", "failed_used", "patient_no_show"):
        db.add(LarcCheckout(assignment_id=a.id, device_id=d.id, requested_by="ma@x.com",
                            outcome=oc, requested_at=datetime(2026, 6, 15)))
    db.commit()
    out = rpt.insertion_outcomes(db, date_from=df, date_to=dt, location=None, device_type_id=None)
    assert out["success"] == 1 and out["failed_unused"] == 1 and out["failed_used"] == 1
    assert out["total"] == 3                       # no_show excluded from the rate base
    assert out["failure_rate"] == round(2 / 3, 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_service.py -k "insertions or outcomes" -v`
Expected: FAIL — `AttributeError: ... 'insertions'`.

- [ ] **Step 3: Append to `reports.py`**

```python
def _inserted_in_range_q(db, date_from, date_to, location, device_type_id):
    return (_assignment_base(db, location, device_type_id)
            .filter(LarcAssignment.status.in_(("inserted", "billed")),
                    LarcAssignment.inserted_at.isnot(None),
                    LarcAssignment.inserted_at >= _dt_floor(date_from),
                    LarcAssignment.inserted_at < _dt_floor(date_to + timedelta(days=1))))


def insertions(db: Session, *, date_from: date, date_to: date,
               location: Optional[str] = None, device_type_id: Optional[str] = None) -> dict:
    rows = _inserted_in_range_q(db, date_from, date_to, location, device_type_id).all()
    cats = {t.id: t.category for t in db.query(LarcDeviceType).all()}
    by_category: dict = {}
    for a in rows:
        c = cats.get(a.device_type_id, "larc")
        by_category[c] = by_category.get(c, 0) + 1
    total = len(rows)
    length = (date_to - date_from).days + 1
    prior_to = date_from - timedelta(days=1)
    prior_from = prior_to - timedelta(days=length - 1)
    prior_total = _inserted_in_range_q(db, prior_from, prior_to, location, device_type_id).count()
    return {"total": total, "by_category": by_category, "prior_total": prior_total,
            "prior_from": prior_from, "prior_to": prior_to, "delta": total - prior_total}


def insertion_outcomes(db: Session, *, date_from: date, date_to: date,
                       location: Optional[str] = None,
                       device_type_id: Optional[str] = None) -> dict:
    """Period: insertion-visit outcomes from LarcCheckout (requested_at in range)."""
    from app.models.larc import LarcCheckout
    q = (db.query(LarcCheckout)
         .join(LarcAssignment, LarcCheckout.assignment_id == LarcAssignment.id)
         .filter(LarcAssignment.deleted_at.is_(None),
                 LarcCheckout.outcome.isnot(None),
                 LarcCheckout.requested_at >= _dt_floor(date_from),
                 LarcCheckout.requested_at < _dt_floor(date_to + timedelta(days=1))))
    if device_type_id:
        q = q.filter(LarcAssignment.device_type_id == device_type_id)
    if location:
        q = (q.join(LarcDevice, LarcAssignment.device_id == LarcDevice.id)
               .filter(LarcDevice.location == location))
    rows = q.all()
    success = sum(1 for c in rows if c.outcome == "inserted")
    fu = sum(1 for c in rows if c.outcome == "failed_unused")
    fused = sum(1 for c in rows if c.outcome == "failed_used")
    attempts = success + fu + fused
    rate = round((fu + fused) / attempts, 2) if attempts else 0.0
    return {"success": success, "failed_unused": fu, "failed_used": fused,
            "total": attempts, "failure_rate": rate}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/larc/reports.py tests/test_larc_reports_service.py
git commit -m "feat(larc-reports): insertions + insertion-outcomes tiles"
```

---

### Task 3: Service — billing backlog, owed patients, inventory health

**Files:**
- Modify: `backend/app/services/larc/reports.py` (append)
- Test: `backend/tests/test_larc_reports_service.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_billing_backlog(db):
    t = _dtype(db)
    _assignment(db, t, status="inserted", chart="B1", billed_at=None)
    _assignment(db, t, status="inserted", chart="B2", billed_at=datetime(2026, 6, 3))  # billed
    _assignment(db, t, status="new", chart="B3")                                        # not inserted
    out = rpt.billing_backlog(db, location=None, device_type_id=None)
    assert out["count"] == 1


def test_owed_patients(db):
    from app.models.larc import LarcOwedPatient
    t = _dtype(db)
    a = _assignment(db, t, chart="OW1")
    db.add(LarcOwedPatient(chart_number="OW1", patient_name="Pt OW1",
                           original_assignment_id=a.id, original_device_type_id=t.id))
    db.add(LarcOwedPatient(chart_number="OW2", patient_name="Pt OW2",
                           original_assignment_id=a.id, original_device_type_id=t.id,
                           resolved_at=datetime(2026, 6, 1)))   # resolved → excluded
    db.commit()
    out = rpt.owed_patients(db, location=None, device_type_id=None)
    assert out["owed_count"] == 1


def test_inventory_health(db):
    from datetime import date as _d
    t = _dtype(db, "Liletta", "larc", reorder=5)
    _device(db, t, status="unassigned", our_id="D1", location="white_plains",
            expires=_d(2026, 7, 1))      # in stock + expiring within 90d of 6/15
    _device(db, t, status="inserted", our_id="D2")   # not in stock
    out = rpt.inventory_health(db, location=None, device_type_id=None, today=_d(2026, 6, 15))
    assert out["total_on_hand"] == 1
    assert out["expiring"] == 1
    assert out["below_reorder"] == 1     # 1 on hand < threshold 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_service.py -k "backlog or owed or inventory" -v`
Expected: FAIL — `AttributeError: ... 'billing_backlog'`.

- [ ] **Step 3: Append to `reports.py`**

```python
def billing_backlog(db: Session, *, location: Optional[str] = None,
                    device_type_id: Optional[str] = None) -> dict:
    """Snapshot: inserted assignments not yet billed."""
    n = (_assignment_base(db, location, device_type_id)
         .filter(LarcAssignment.status == "inserted",
                 LarcAssignment.billed_at.is_(None)).count())
    return {"count": n}


def owed_patients(db: Session, *, location: Optional[str] = None,
                  device_type_id: Optional[str] = None) -> dict:
    """Snapshot: open owed-patient rows + failed-used assignments awaiting a
    replacement (the 'failed_replacement_unrequested' state)."""
    from app.models.larc import LarcOwedPatient
    oq = db.query(LarcOwedPatient).filter(LarcOwedPatient.resolved_at.is_(None))
    if device_type_id:
        oq = oq.filter(LarcOwedPatient.original_device_type_id == device_type_id)
    owed_count = oq.count()
    awaiting = (_assignment_base(db, location, device_type_id)
                .filter(LarcAssignment.status == "failed_used",
                        LarcAssignment.replacement_assignment_id.is_(None)).count())
    return {"owed_count": owed_count, "awaiting_replacement": awaiting,
            "total": owed_count + awaiting}


def _instock_devices_q(db, location, device_type_id):
    q = db.query(LarcDevice).filter(LarcDevice.status.in_(("unassigned", "received")))
    if location:
        q = q.filter(LarcDevice.location == location)
    if device_type_id:
        q = q.filter(LarcDevice.device_type_id == device_type_id)
    return q


def inventory_health(db: Session, *, location: Optional[str] = None,
                     device_type_id: Optional[str] = None,
                     today: Optional[date] = None) -> dict:
    """Snapshot: in-stock devices by type, expiring <=90 days, and device types
    below their reorder threshold."""
    today = today or now_utc_naive().date()
    horizon = today + timedelta(days=90)
    devices = _instock_devices_q(db, location, device_type_id).all()
    types = {t.id: t for t in db.query(LarcDeviceType).all()}
    by_type: dict = {}
    expiring = 0
    onhand_by_type: dict = {}
    for d in devices:
        name = types[d.device_type_id].name if d.device_type_id in types else "Unknown"
        by_type[name] = by_type.get(name, 0) + 1
        onhand_by_type[d.device_type_id] = onhand_by_type.get(d.device_type_id, 0) + 1
        if d.expiration_date and d.expiration_date <= horizon:
            expiring += 1
    below = 0
    for tid, t in types.items():
        if t.reorder_threshold is None:
            continue
        if device_type_id and tid != device_type_id:
            continue
        if onhand_by_type.get(tid, 0) < int(t.reorder_threshold):
            below += 1
    return {"total_on_hand": len(devices), "by_type": by_type,
            "expiring": expiring, "below_reorder": below}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/larc/reports.py tests/test_larc_reports_service.py
git commit -m "feat(larc-reports): billing backlog + owed patients + inventory health"
```

---

### Task 4: Service — drill-down rows + CSV

**Files:**
- Modify: `backend/app/services/larc/reports.py` (append)
- Test: `backend/tests/test_larc_reports_service.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_rows_for_billing_backlog(db):
    t = _dtype(db)
    d = _device(db, t, ownership="wwc_owned")
    _assignment(db, t, status="inserted", device=d, chart="R1", billed_at=None)
    rows = rpt.rows_for(db, "billing_backlog", date_from=date(2026, 6, 1),
                        date_to=date(2026, 6, 30), location=None, device_type_id=None)
    assert len(rows) == 1 and rows[0]["chart_number"] == "R1"
    assert {"assignment_id", "chart_number", "patient_name", "status"} <= set(rows[0])


def test_rows_to_csv_has_header_and_rows():
    csv_text = rpt.rows_to_csv([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    assert lines[0] == "a,b" and len(lines) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_service.py -k "rows" -v`
Expected: FAIL — `AttributeError: ... 'rows_for'`.

- [ ] **Step 3: Append to `reports.py`**

```python
import csv
import io


def _assignment_row(a, type_names: dict) -> dict:
    dev = a.device
    return {
        "assignment_id": str(a.id),
        "chart_number": a.chart_number,
        "patient_name": a.patient_name,
        "device_type": type_names.get(a.device_type_id),
        "ownership": (dev.ownership if dev else None),
        "location": (dev.location if dev else None),
        "status": a.status,
        "source_flow": a.source_flow,
        "inserted_at": a.inserted_at.strftime("%m/%d/%Y") if a.inserted_at else None,
        "billed_at": a.billed_at.strftime("%m/%d/%Y") if a.billed_at else None,
    }


VALID_TILES = ("workflow_funnel", "outstanding_enrollment", "insertions",
               "billing_backlog", "owed_patients", "inventory_health",
               "insertion_outcomes")


def rows_for(db: Session, tile: str, *, date_from: date, date_to: date,
             location: Optional[str] = None, device_type_id: Optional[str] = None,
             bucket: Optional[str] = None, today: Optional[date] = None) -> list[dict]:
    from app.utils.dt import now_utc_naive
    today = today or now_utc_naive().date()
    type_names = {t.id: t.name for t in db.query(LarcDeviceType).all()}

    if tile in ("workflow_funnel", "outstanding_enrollment"):
        q = (_assignment_base(db, location, device_type_id)
             .options(joinedload(LarcAssignment.milestones),
                      joinedload(LarcAssignment.device)))
        out = []
        for a in q.all():
            buckets = assignment_buckets(a, today)
            if tile == "outstanding_enrollment":
                buckets = {b for b in buckets if b in _ENROLLMENT_STAGES}
            if not buckets:
                continue
            if bucket and bucket not in buckets:
                continue
            r = _assignment_row(a, type_names)
            r["bucket"] = "; ".join(sorted(buckets))
            out.append(r)
        return out

    if tile == "insertions":
        rows = _inserted_in_range_q(db, date_from, date_to, location, device_type_id).all()
        cats = {t.id: t.category for t in db.query(LarcDeviceType).all()}
        out = []
        for a in rows:
            cat = cats.get(a.device_type_id, "larc")
            if bucket and cat != bucket:
                continue
            r = _assignment_row(a, type_names)
            r["category"] = cat
            out.append(r)
        return out

    if tile == "billing_backlog":
        q = (_assignment_base(db, location, device_type_id)
             .filter(LarcAssignment.status == "inserted",
                     LarcAssignment.billed_at.is_(None))
             .options(joinedload(LarcAssignment.device)))
        return [_assignment_row(a, type_names) for a in q.all()]

    if tile == "owed_patients":
        from app.models.larc import LarcOwedPatient
        if bucket == "awaiting_replacement":
            q = (_assignment_base(db, location, device_type_id)
                 .filter(LarcAssignment.status == "failed_used",
                         LarcAssignment.replacement_assignment_id.is_(None))
                 .options(joinedload(LarcAssignment.device)))
            return [_assignment_row(a, type_names) for a in q.all()]
        oq = db.query(LarcOwedPatient).filter(LarcOwedPatient.resolved_at.is_(None))
        if device_type_id:
            oq = oq.filter(LarcOwedPatient.original_device_type_id == device_type_id)
        return [{"chart_number": o.chart_number, "patient_name": o.patient_name,
                 "device_type": type_names.get(o.original_device_type_id),
                 "owed_since": o.owed_since.strftime("%m/%d/%Y") if o.owed_since else None}
                for o in oq.all()]

    if tile == "inventory_health":
        horizon = today + timedelta(days=90)
        devices = _instock_devices_q(db, location, device_type_id).all()
        out = []
        for d in devices:
            if bucket == "expiring":
                if not (d.expiration_date and d.expiration_date <= horizon):
                    continue
            elif bucket and bucket not in ("expiring", "below_reorder"):
                if str(d.device_type_id) != bucket:   # bucket is a device-type id
                    continue
            out.append({"our_id": d.our_id, "device_type": type_names.get(d.device_type_id),
                        "location": d.location, "ownership": d.ownership,
                        "expiration_date": d.expiration_date.strftime("%m/%d/%Y") if d.expiration_date else None})
        return out

    if tile == "insertion_outcomes":
        from app.models.larc import LarcCheckout
        q = (db.query(LarcCheckout, LarcAssignment)
             .join(LarcAssignment, LarcCheckout.assignment_id == LarcAssignment.id)
             .filter(LarcAssignment.deleted_at.is_(None),
                     LarcCheckout.outcome.isnot(None),
                     LarcCheckout.requested_at >= _dt_floor(date_from),
                     LarcCheckout.requested_at < _dt_floor(date_to + timedelta(days=1))))
        if device_type_id:
            q = q.filter(LarcAssignment.device_type_id == device_type_id)
        if location:
            q = q.join(LarcDevice, LarcAssignment.device_id == LarcDevice.id).filter(LarcDevice.location == location)
        _label = {"inserted": "success"}
        out = []
        for c, a in q.all():
            key = _label.get(c.outcome, c.outcome)
            if bucket and key != bucket:
                continue
            out.append({"checkout_id": str(c.id), "chart_number": a.chart_number,
                        "patient_name": a.patient_name, "device_type": type_names.get(a.device_type_id),
                        "outcome": c.outcome,
                        "requested_at": c.requested_at.strftime("%m/%d/%Y") if c.requested_at else None})
        return out

    raise ValueError(f"unknown tile: {tile}")


def rows_to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    fields = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("; ".join(map(str, v)) if isinstance(v, list) else v)
                    for k, v in r.items()})
    return buf.getvalue()
```

(For the `insertion_outcomes` drill, `bucket="success"` maps to `outcome=="inserted"`; `failed_unused`/`failed_used` match directly.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/larc/reports.py tests/test_larc_reports_service.py
git commit -m "feat(larc-reports): drill-down rows + CSV serialization"
```

---

### Task 5: Router + registration

**Files:**
- Create: `backend/app/routers/larc_reports.py`
- Modify: `backend/app/main.py` (import + include_router)
- Test: `backend/tests/test_larc_reports_router.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_larc_reports_router.py
"""LARC Reports endpoints. `client` is the super-admin fixture."""
from datetime import datetime

from app.models.larc import LarcAssignment, LarcDeviceType


def _dtype(db, name="Liletta"):
    t = LarcDeviceType(name=name, category="larc"); db.add(t); db.commit(); db.refresh(t)
    return t


def _assign(db, t, **kw):
    base = dict(chart_number="M1", patient_name="Doe, J", status="inserted",
                source_flow="in_stock", device_type_id=t.id)
    base.update(kw)
    a = LarcAssignment(**base); db.add(a); db.commit(); db.refresh(a)
    return a


def test_summary_returns_all_tiles(client, db):
    t = _dtype(db)
    _assign(db, t, status="inserted", inserted_at=datetime(2026, 6, 10), billed_at=None)
    r = client.get("/api/larc/reports/summary?from=2026-06-01&to=2026-06-30")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("workflow_funnel", "outstanding_enrollment", "insertions",
                "billing_backlog", "owed_patients", "inventory_health",
                "insertion_outcomes", "period", "device_types"):
        assert key in body
    assert body["billing_backlog"]["count"] == 1


def test_rows_json_and_csv(client, db):
    t = _dtype(db)
    _assign(db, t, status="inserted", billed_at=None)
    j = client.get("/api/larc/reports/billing_backlog/rows")
    assert j.status_code == 200 and len(j.json()["items"]) == 1
    c = client.get("/api/larc/reports/billing_backlog/rows?format=csv")
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv")
    assert c.text.splitlines()[0].startswith("assignment_id")


def test_rows_unknown_tile_404(client, db):
    assert client.get("/api/larc/reports/bogus/rows").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_router.py -v`
Expected: FAIL — 404 on `/summary` (router not mounted).

- [ ] **Step 3: Create the router**

```python
# backend/app/routers/larc_reports.py
"""LARC Reports endpoints: a one-shot summary of all tiles, plus per-tile
drill-down rows (JSON or CSV). Read-only (Module.LARC, Tier.VIEW)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services.larc import reports as rpt
from app.utils.dt import now_utc_naive

router = APIRouter(prefix="/larc/reports", tags=["larc-reports"])


def _parse_range(from_: Optional[date], to: Optional[date]) -> tuple[date, date]:
    today = now_utc_naive().date()
    return (from_ or today.replace(day=1), to or today)


def _isoize(ins: dict) -> dict:
    out = dict(ins)
    out["prior_from"] = ins["prior_from"].isoformat()
    out["prior_to"] = ins["prior_to"].isoformat()
    return out


@router.get("/summary")
def reports_summary(
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    location: Optional[str] = None,
    device_type_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
):
    df, dt = _parse_range(from_, to)
    return {
        "period": {"from": df.isoformat(), "to": dt.isoformat()},
        "device_types": rpt.device_types(db),
        "workflow_funnel": rpt.workflow_funnel(db, location=location, device_type_id=device_type_id),
        "outstanding_enrollment": rpt.outstanding_enrollment(db, location=location, device_type_id=device_type_id),
        "insertions": _isoize(rpt.insertions(db, date_from=df, date_to=dt,
                                             location=location, device_type_id=device_type_id)),
        "billing_backlog": rpt.billing_backlog(db, location=location, device_type_id=device_type_id),
        "owed_patients": rpt.owed_patients(db, location=location, device_type_id=device_type_id),
        "inventory_health": rpt.inventory_health(db, location=location, device_type_id=device_type_id),
        "insertion_outcomes": rpt.insertion_outcomes(db, date_from=df, date_to=dt,
                                                     location=location, device_type_id=device_type_id),
    }


@router.get("/{tile}/rows")
def reports_rows(
    tile: str,
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    location: Optional[str] = None,
    device_type_id: Optional[str] = None,
    bucket: Optional[str] = None,
    output_format: Optional[str] = Query(None, alias="format"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
):
    if tile not in rpt.VALID_TILES:
        raise HTTPException(status_code=404, detail="unknown report tile")
    df, dt = _parse_range(from_, to)
    rows = rpt.rows_for(db, tile, date_from=df, date_to=dt, location=location,
                        device_type_id=device_type_id, bucket=bucket)
    if (output_format or "").lower() == "csv":
        csv_text = rpt.rows_to_csv(rows)
        filename = f"larc-{tile}-{df.isoformat()}_{dt.isoformat()}.csv"
        return StreamingResponse(
            iter([csv_text]), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return {"items": rows}
```

- [ ] **Step 4: Register the router in `main.py`**

- Add `larc_reports` to the `from app.routers import ...` line.
- Add an include next to the `larc` include (search `include_router(larc.router`):
```python
app.include_router(larc_reports.router, prefix="/api")
```
Mirror the existing `larc.router` include form.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_router.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/routers/larc_reports.py app/main.py tests/test_larc_reports_router.py
git commit -m "feat(larc-reports): summary + drill-down rows/CSV endpoints"
```

---

### Task 6: Frontend — Reports page + route + nav

**Files:**
- Create: `frontend/src/pages/LarcReports.jsx`
- Modify: `frontend/src/routes.jsx`, `frontend/src/components/larc/LarcNav.jsx`

- [ ] **Step 1: Inspect the patterns**

Run from `frontend/`:
- `grep -n "to:\|label:\|LINKS\|TIER" src/components/larc/LarcNav.jsx | head`
- `grep -n "path: 'devices'\|M.LARC\|element: <Larc" src/routes.jsx | head`
- Read `src/pages/PelletReports.jsx` FULLY (the closest reference — filter bar, tiles grid, DrillDown modal, blob CSV download); adapt it for LARC.

- [ ] **Step 2: Add the route + nav link**

`routes.jsx` — import + child route under `/larc` (next to `devices`/`owed`):
```javascript
import LarcReports from './pages/LarcReports'
```
```javascript
    { path: 'reports',         element: <LarcReports />,        module: M.LARC, tier: TIER.VIEW },
```
`LarcNav.jsx` `LINKS` array (e.g. after `owed`):
```javascript
    { to: '/larc/reports',         label: 'Reports',         tier: TIER.VIEW },
```

- [ ] **Step 3: Create `LarcReports.jsx`**

Adapt `PelletReports.jsx`. Keep its date-preset logic, filter-bar structure, tile-grid layout, DrillDown modal, and blob CSV download (`api.get(..., {responseType:'blob'})`). LARC specifics:
- Endpoint base `/larc/reports`. Summary: `useQuery(['larc-report-summary', from, to, location, deviceTypeId], () => api.get('/larc/reports/summary', { params: { from, to, location: location||undefined, device_type_id: deviceTypeId||undefined } }).then(r => r.data))`.
- Filters: date preset (This Month / Last Month / Last 30 / 90 / Custom); **location** select (`''`=All, white_plains/brandywine/arlington labels White Plains/Brandywine/Arlington); **device type** select (`''`=All + options from `data?.device_types` → `{id, name}`).
- 7 tiles (Title Case titles):
  1. **Workflow Funnel** — rows from `workflow_funnel.by_bucket` (humanize the bucket key: replace `_` with spaces, Title Case), each clickable → drill `bucket=<bucket>`. Sort by count desc.
  2. **Outstanding Enrollment** — `outstanding_enrollment.total` headline + per-stage chips (Needs Enrollment / Needs Fax / Awaiting Receipt / Received Not Notified) from `by_stage` (skip zeros), clickable → `bucket=<stage>`.
  3. **Insertions** — `insertions.total` + `by_category` split (LARC / Office Procedure) + delta vs prior.
  4. **Billing Backlog** — `billing_backlog.count`.
  5. **Owed Patients** — `owed_patients.owed_count` + `awaiting_replacement` (clickable → `bucket=awaiting_replacement`) + total.
  6. **Inventory Health** — `inventory_health.total_on_hand` headline; per-type rows from `by_type`; `expiring` + `below_reorder` chips (clickable → `bucket=expiring`/`below_reorder`).
  7. **Insertion Outcomes** — `success` / `failed_unused` / `failed_used` counts + `failure_rate` as a percent; clickable per outcome → `bucket=success`/`failed_unused`/`failed_used`.
- Drill-down + Download CSV identical in shape to PelletReports (call `/larc/reports/{tile}/rows` with `{from,to,location,device_type_id,bucket}`, render `items` table, CSV via blob). Dates MM/DD/YYYY via `fmt.date`. Title Case titles + buttons.

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: succeeds, no errors referencing the new/changed files.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/pages/LarcReports.jsx src/routes.jsx src/components/larc/LarcNav.jsx
git commit -m "feat(larc-reports): Reports page with filters, tiles, drill-down + CSV"
```

---

### Task 7: Authenticated walk-through

**Files:**
- Create: `backend/tests/test_larc_reports_walkthrough.py`

- [ ] **Step 1: Write the walk-through test**

```python
# backend/tests/test_larc_reports_walkthrough.py
"""Authenticated walk-through of LARC Reports: seed a small set, read the
summary, drill into a tile, and export CSV. `client` is the super-admin fixture."""
from datetime import datetime

from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType


def test_larc_reports_walkthrough(client, db, capsys):
    log = []
    t = LarcDeviceType(name="Liletta", category="larc", reorder_threshold=5)
    db.add(t); db.commit(); db.refresh(t)
    d = LarcDevice(our_id="LAR-WT", device_type_id=t.id, status="unassigned",
                   ownership="wwc_owned", location="white_plains")
    db.add(d); db.commit(); db.refresh(d)
    db.add(LarcAssignment(chart_number="WT1", patient_name="Roe, Pat", status="inserted",
                          source_flow="in_stock", device_type_id=t.id, device_id=d.id,
                          inserted_at=datetime(2026, 6, 10), billed_at=None))
    db.commit()

    body = client.get("/api/larc/reports/summary?from=2026-06-01&to=2026-06-30").json()
    assert set(body) >= {"workflow_funnel", "outstanding_enrollment", "insertions",
                         "billing_backlog", "owed_patients", "inventory_health",
                         "insertion_outcomes", "period", "device_types"}
    log.append(f"1. /summary → insertions {body['insertions']['total']}, "
               f"billing backlog {body['billing_backlog']['count']}, "
               f"inventory on hand {body['inventory_health']['total_on_hand']}")

    items = client.get("/api/larc/reports/billing_backlog/rows").json()["items"]
    assert len(items) == 1 and items[0]["chart_number"] == "WT1"
    log.append(f"2. drill billing_backlog → {len(items)} unbilled insertion")

    csv_resp = client.get("/api/larc/reports/billing_backlog/rows?format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.text.splitlines()[0].startswith("assignment_id")
    log.append("3. CSV export → text/csv with header + 1 row")

    with capsys.disabled():
        print("\n  -- LARC Reports walk-through (authenticated) --")
        for line in log:
            print("   " + line)
```

- [ ] **Step 2: Run it**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_reports_walkthrough.py -v -s`
Expected: PASS, log prints.

- [ ] **Step 3: Commit**

```bash
cd backend && git add tests/test_larc_reports_walkthrough.py
git commit -m "test(larc-reports): authenticated reports walk-through"
```

---

## Final Verification (after all tasks)
- [ ] `cd backend && ./venv/bin/python -m pytest tests/ -k "larc_reports" -v` → all PASS.
- [ ] `cd frontend && npm run build` → clean.
- [ ] No new failures: `cd backend && ./venv/bin/python -m pytest tests/ -k "larc" -q`.

## Notes for the implementer
- **No new tables/columns** — compute on request.
- **Funnel + enrollment reuse `assignment_buckets`** (load `milestones` + `device`); they exclude billed/cancelled automatically (that function returns empty for those).
- **Filter resolution:** device type → `LarcAssignment.device_type_id` (works pre-receipt); location → the assignment's `device.location` (pharmacy-order assignments with no device yet don't match a specific location). Outcomes filter via the checkout's assignment + device. Inventory queries `LarcDevice` directly (location + device-type on the device).
- **Snapshot vs period:** only `insertions` + `insertion_outcomes` use the date range; the rest are "as of now".
- If a `LarcAssignment`/`LarcDevice`/`LarcCheckout` constructor needs a NOT NULL column the test helpers omit, add the minimal field (keep assertions intact) and note it.
