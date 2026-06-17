# Pellet Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Pellet → Reports page: a filter bar (date range + location + provider) over 6 tiles (visit funnel, insertions, recall due, prerequisites, billing backlog, inventory health), each clickable to a drill-down list with CSV export.

**Architecture:** A pure aggregation service (`app/services/pellet/reports.py`, one fn per tile) feeds a dedicated router (`app/routers/pellet_reports.py`, prefix `/pellets/reports`, `Tier.VIEW`) exposing `/summary`, `/{tile}/rows`, and `?format=csv`. A new React page (`PelletReports.jsx`) is wired into the pellet routes + nav, mirroring the just-shipped Surgery Reports.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest (backend); React + react-query + Vite (frontend).

**Spec:** `docs/superpowers/specs/2026-06-17-pellet-reports-design.md`

**Conventions:** MM/DD/YYYY, Title Case, money `$X.XX`; `now_utc_naive()` never `datetime.utcnow()`; backend pytest via `./venv/bin/python -m pytest ...` from `backend/`; frontend `npm run build` from `frontend/`.

**Grounding facts (verified):**
- `PelletVisit` (`app/models/pellet.py`): `id, patient_id, visit_kind (initial|booster|repeat), status (new|in_progress|inserted|billed|cancelled|rescheduled), scheduled_date (Date), location, provider, inserted_at (DateTime), billed_at (DateTime), price_amount (Numeric), is_historical (Boolean), created_at`; `patient` relationship; `doses` relationship.
- `PelletPatient`: `id, chart_number, patient_name, status (active|inactive|declined), recall_interval_months (Int, default 4), mammo_verified, mammo_date (Date), labs_verified, labs_not_required, labs_date (Date)`; `visits` relationship.
- `PelletConsent` (`app/models/pellet_portal.py`): `pellet_patient_id, status, expires_at`; `@property is_valid` = `status=="signed" and expires_at and expires_at > now_utc_naive()`.
- `PelletStock`: `lot_id (FK PelletLot), location, doses_on_hand (Int), status (active)`. `PelletLot`: `id, dose_type_id (FK), qualgen_lot_number, expiration_date (Date)`. `PelletDoseType`: `id, hormone, dose_mg, label, reorder_thresholds_by_location (JSON, per-location ints or null)`.
- `cfg(db, key)` in `app/services/pellet/settings.py`: `mammo_valid_days`=365, `labs_valid_days`=14, `require_mammo`=True, `require_labs`=True.
- Locations: `white_plains`, `brandywine`, `arlington` (labels White Plains / Brandywine / Arlington).
- Pellet router prefix `/pellets`; app mounts under `/api`. `Module.PELLETS`, `Tier.VIEW`. `/pellets/picklists` returns `locations` + `disposal_reasons` (NO providers — the reports summary exposes a `providers` list instead).
- Frontend: pellet nav `frontend/src/components/pellet/PelletNav.jsx` (`LINKS` array of `{to, label, tier}`); routes in `frontend/src/routes.jsx` (children under the `/pellets` `PelletNav` route, e.g. `{ path: 'activity', element: <PelletActivity/>, module: M.PELLETS, tier: TIER.VIEW }`). `api`/`fmt` from `../utils/api`; `SurgeryReports.jsx` is the reference page.
- Tests: function-scoped empty `db`; super-admin `client`. Creating model rows directly via `db` works.

---

## File Structure
- Create `backend/app/services/pellet/reports.py` — aggregations + drill-down rows + CSV.
- Create `backend/app/routers/pellet_reports.py` — endpoints; register in `app/main.py`.
- Create `frontend/src/pages/PelletReports.jsx`; add a route in `routes.jsx` + a nav link in `PelletNav.jsx`.
- Tests: `backend/tests/test_pellet_reports_service.py`, `test_pellet_reports_router.py`, `test_pellet_reports_walkthrough.py`.

---

### Task 1: Service — base query, status funnel, insertions, providers

**Files:**
- Create: `backend/app/services/pellet/reports.py`
- Test: `backend/tests/test_pellet_reports_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_reports_service.py
"""Pellet Reports aggregation service."""
from datetime import date, datetime, timedelta

from app.models.pellet import PelletPatient, PelletVisit
from app.services.pellet import reports as rpt


def _patient(db, **kw):
    base = dict(chart_number="PC1", patient_name="Doe, J", status="active")
    base.update(kw)
    p = PelletPatient(**base); db.add(p); db.commit(); db.refresh(p)
    return p


def _visit(db, p, **kw):
    base = dict(patient_id=p.id, visit_kind="initial", status="new",
                location="white_plains", provider="Cooke, Aryian, MD")
    base.update(kw)
    v = PelletVisit(**base); db.add(v); db.commit(); db.refresh(v)
    return v


def test_status_funnel_counts_and_filters(db):
    p = _patient(db)
    _visit(db, p, status="new")
    _visit(db, p, status="inserted")
    _visit(db, p, status="inserted", location="arlington")
    out = rpt.status_funnel(db, location=None, provider=None)
    assert out["by_status"]["inserted"] == 2
    assert out["by_status"]["new"] == 1
    out2 = rpt.status_funnel(db, location="arlington", provider=None)
    assert out2["by_status"]["inserted"] == 1 and out2["by_status"].get("new", 0) == 0


def test_status_funnel_excludes_historical(db):
    p = _patient(db)
    _visit(db, p, status="inserted")
    _visit(db, p, status="inserted", is_historical=True)
    assert rpt.status_funnel(db, location=None, provider=None)["by_status"]["inserted"] == 1


def test_insertions_in_range_with_prior(db):
    p = _patient(db)
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    _visit(db, p, status="inserted", visit_kind="initial", inserted_at=datetime(2026, 6, 10))
    _visit(db, p, status="billed", visit_kind="booster", inserted_at=datetime(2026, 6, 20))
    _visit(db, p, status="inserted", visit_kind="initial", inserted_at=datetime(2026, 5, 15))
    out = rpt.insertions(db, date_from=df, date_to=dt, location=None, provider=None)
    assert out["total"] == 2
    assert out["by_kind"] == {"initial": 1, "booster": 1}
    assert out["prior_total"] == 1
    assert out["delta"] == 1
    assert out["prior_from"] == date(2026, 5, 2) and out["prior_to"] == date(2026, 5, 31)


def test_providers_lists_distinct(db):
    p = _patient(db)
    _visit(db, p, provider="Cooke, Aryian, MD")
    _visit(db, p, provider="Smith, Pat, NP")
    _visit(db, p, provider="Cooke, Aryian, MD")
    assert rpt.providers(db) == ["Cooke, Aryian, MD", "Smith, Pat, NP"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.pellet.reports'`.

- [ ] **Step 3: Create the service**

```python
# backend/app/services/pellet/reports.py
"""Pellet Reports aggregations. Each tile is a pure function over the pellet
data, parameterized by an optional location + provider filter and (for period
tiles) a date range. No persistence; computed on request."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.pellet import PelletVisit


def _visit_base(db: Session, location: Optional[str], provider: Optional[str]):
    """PelletVisit query excluding historical-import rows, with optional
    location/provider filters."""
    q = db.query(PelletVisit).filter(PelletVisit.is_historical.is_(False))
    if location:
        q = q.filter(PelletVisit.location == location)
    if provider:
        q = q.filter(PelletVisit.provider == provider)
    return q


def _dt_floor(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _inserted_in_range_q(db, date_from, date_to, location, provider):
    return (_visit_base(db, location, provider)
            .filter(PelletVisit.status.in_(("inserted", "billed")),
                    PelletVisit.inserted_at.isnot(None),
                    PelletVisit.inserted_at >= _dt_floor(date_from),
                    PelletVisit.inserted_at < _dt_floor(date_to + timedelta(days=1))))


def status_funnel(db: Session, *, location: Optional[str] = None,
                  provider: Optional[str] = None) -> dict:
    rows = (_visit_base(db, location, provider)
            .with_entities(PelletVisit.status, func.count(PelletVisit.id))
            .group_by(PelletVisit.status).all())
    return {"by_status": {status: int(n) for status, n in rows}}


def insertions(db: Session, *, date_from: date, date_to: date,
               location: Optional[str] = None, provider: Optional[str] = None) -> dict:
    rows = (_inserted_in_range_q(db, date_from, date_to, location, provider)
            .with_entities(PelletVisit.visit_kind, func.count(PelletVisit.id))
            .group_by(PelletVisit.visit_kind).all())
    by_kind = {(k or "unspecified"): int(n) for k, n in rows}
    total = sum(by_kind.values())
    length = (date_to - date_from).days + 1
    prior_to = date_from - timedelta(days=1)
    prior_from = prior_to - timedelta(days=length - 1)
    prior_total = _inserted_in_range_q(db, prior_from, prior_to, location, provider).count()
    return {"total": total, "by_kind": by_kind, "prior_total": prior_total,
            "prior_from": prior_from, "prior_to": prior_to, "delta": total - prior_total}


def providers(db: Session) -> list[str]:
    """Distinct non-null providers across non-historical visits (for the filter
    dropdown — independent of the date range)."""
    rows = (db.query(PelletVisit.provider)
            .filter(PelletVisit.is_historical.is_(False),
                    PelletVisit.provider.isnot(None))
            .distinct().all())
    return sorted({r[0] for r in rows if r[0]})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/pellet/reports.py tests/test_pellet_reports_service.py
git commit -m "feat(pellet-reports): service base + status funnel, insertions, providers"
```

---

### Task 2: Service — recall due + prerequisites

**Files:**
- Modify: `backend/app/services/pellet/reports.py` (append)
- Test: `backend/tests/test_pellet_reports_service.py` (append)

`recall_due` mirrors the existing `recall_is_due` logic (`pellet.py` ~3550-3589): effective visit date `d = inserted_at.date() if inserted_at else scheduled_date`; `last_visit_dt` = max `d`; `due = last + (recall_interval_months or 4)*30 days`; `active` = patient has an open visit (status not in billed/cancelled). overdue = `due < today and not active`; due_soon = `today <= due <= today+30 and not active`. `prerequisites` reuses `cfg()` windows + `PelletConsent.is_valid`.

- [ ] **Step 1: Append the failing test**

```python
def test_recall_due_overdue_and_due_soon(db):
    from app.models.pellet import PelletPatient, PelletVisit
    today = date(2026, 6, 15)
    # Overdue: last insertion 200 days ago, interval 4mo (120d), no open visit.
    p1 = _patient(db, chart_number="R1", recall_interval_months=4)
    _visit(db, p1, status="billed", inserted_at=datetime(2026, 6, 15) - timedelta(days=200))
    # Due soon: last insertion 110 days ago, interval 4mo → due in ~10 days.
    p2 = _patient(db, chart_number="R2", recall_interval_months=4)
    _visit(db, p2, status="billed", inserted_at=datetime(2026, 6, 15) - timedelta(days=110))
    # Not due: last insertion 10 days ago.
    p3 = _patient(db, chart_number="R3", recall_interval_months=4)
    _visit(db, p3, status="billed", inserted_at=datetime(2026, 6, 15) - timedelta(days=10))
    # Overdue-by-date BUT has an open future visit → not counted.
    p4 = _patient(db, chart_number="R4", recall_interval_months=4)
    _visit(db, p4, status="billed", inserted_at=datetime(2026, 6, 15) - timedelta(days=200))
    _visit(db, p4, status="new", scheduled_date=date(2026, 6, 20))
    out = rpt.recall_due(db, location=None, provider=None, today=today)
    assert out["overdue"] == 1     # only p1
    assert out["due_soon"] == 1    # only p2
    assert out["total"] == 2


def test_prerequisites_blockers(db):
    from app.models.pellet import PelletPatient
    today = date(2026, 6, 15)
    # Upcoming visit, patient missing mammo + labs + consent → blocker.
    p = _patient(db, chart_number="PR1", mammo_verified=False, labs_verified=False,
                 labs_not_required=False)
    _visit(db, p, status="new", scheduled_date=date(2026, 6, 20))
    # Upcoming but fully ready → excluded.
    p2 = _patient(db, chart_number="PR2", mammo_verified=True, mammo_date=date(2026, 6, 1),
                  labs_verified=True, labs_date=date(2026, 6, 10))
    from app.models.pellet_portal import PelletConsent
    from app.utils.dt import now_utc_naive
    db.add(PelletConsent(pellet_patient_id=p2.id, status="signed",
                         expires_at=now_utc_naive() + timedelta(days=300)))
    _visit(db, p2, status="new", scheduled_date=date(2026, 6, 18))
    db.commit()
    out = rpt.prerequisites(db, location=None, provider=None, today=today)
    assert out["total"] == 1
    assert out["by_blocker"]["mammo"] == 1
    assert out["by_blocker"]["consent"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_service.py -k "recall or prerequisites" -v`
Expected: FAIL — `AttributeError: ... 'recall_due'`.

- [ ] **Step 3: Append to `reports.py`**

Add imports near the top:
```python
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
```

Add the functions:
```python
def _effective_visit_date(v) -> Optional[date]:
    if v.inserted_at:
        return v.inserted_at.date()
    return v.scheduled_date


def _has_open_visit(visits) -> bool:
    return any(v.status not in ("billed", "cancelled") for v in visits)


def recall_due(db: Session, *, location: Optional[str] = None,
               provider: Optional[str] = None, today: Optional[date] = None) -> dict:
    """Snapshot: active patients past (or nearing) their recall interval, with
    no open visit. Mirrors pellet.py recall_is_due (interval*30 days)."""
    from app.utils.dt import now_utc_naive
    today = today or now_utc_naive().date()
    soon = today + timedelta(days=30)
    overdue = due_soon = 0
    patients = (db.query(PelletPatient)
                .filter(PelletPatient.status == "active").all())
    for p in patients:
        visits = list(p.visits or [])
        if not visits:
            continue
        # location/provider filter: match the patient's latest visit.
        latest = max(visits, key=lambda v: (v.created_at or datetime.min))
        if location and latest.location != location:
            continue
        if provider and latest.provider != provider:
            continue
        dates = [d for d in (_effective_visit_date(v) for v in visits) if d]
        if not dates:
            continue
        last_dt = max(dates)
        interval = p.recall_interval_months or 4
        due = last_dt + timedelta(days=interval * 30)
        if _has_open_visit(visits):
            continue
        if due < today:
            overdue += 1
        elif due <= soon:
            due_soon += 1
    return {"overdue": overdue, "due_soon": due_soon, "total": overdue + due_soon}


def _mammo_ok(db, p, today) -> bool:
    from app.services.pellet.settings import cfg
    if not cfg(db, "require_mammo"):
        return True
    if not (p.mammo_verified and p.mammo_date):
        return False
    return (today - p.mammo_date).days <= int(cfg(db, "mammo_valid_days"))


def _labs_ok(db, p, today) -> bool:
    from app.services.pellet.settings import cfg
    if not cfg(db, "require_labs") or p.labs_not_required:
        return True
    if not (p.labs_verified and p.labs_date):
        return False
    return (today - p.labs_date).days <= int(cfg(db, "labs_valid_days"))


def _consent_ok(db, patient_id) -> bool:
    rows = (db.query(PelletConsent)
            .filter(PelletConsent.pellet_patient_id == patient_id).all())
    return any(c.is_valid for c in rows)


def prerequisites(db: Session, *, location: Optional[str] = None,
                  provider: Optional[str] = None, today: Optional[date] = None) -> dict:
    """Snapshot: upcoming visits (scheduled <=14 days, status new/in_progress)
    whose patient is missing mammo / labs / consent."""
    from app.utils.dt import now_utc_naive
    today = today or now_utc_naive().date()
    horizon = today + timedelta(days=14)
    rows = (_visit_base(db, location, provider)
            .filter(PelletVisit.scheduled_date.isnot(None),
                    PelletVisit.scheduled_date >= today,
                    PelletVisit.scheduled_date <= horizon,
                    PelletVisit.status.in_(("new", "in_progress")))
            .all())
    by_blocker = {"mammo": 0, "labs": 0, "consent": 0}
    total = 0
    for v in rows:
        p = v.patient
        if p is None:
            continue
        blockers = []
        if not _mammo_ok(db, p, today):
            blockers.append("mammo")
        if not _labs_ok(db, p, today):
            blockers.append("labs")
        if not _consent_ok(db, p.id):
            blockers.append("consent")
        if blockers:
            total += 1
            for b in blockers:
                by_blocker[b] += 1
    return {"total": total, "by_blocker": by_blocker}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/pellet/reports.py tests/test_pellet_reports_service.py
git commit -m "feat(pellet-reports): recall-due + prerequisites tiles"
```

---

### Task 3: Service — billing backlog + inventory health

**Files:**
- Modify: `backend/app/services/pellet/reports.py` (append)
- Test: `backend/tests/test_pellet_reports_service.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_billing_backlog(db):
    from decimal import Decimal
    p = _patient(db)
    _visit(db, p, status="inserted", inserted_at=datetime(2026, 6, 1),
           price_amount=Decimal("500.00"), billed_at=None)
    _visit(db, p, status="inserted", inserted_at=datetime(2026, 6, 2),
           price_amount=Decimal("400.00"), billed_at=datetime(2026, 6, 3))  # already billed
    _visit(db, p, status="new")   # not inserted
    out = rpt.billing_backlog(db, location=None, provider=None)
    assert out["count"] == 1
    assert out["total_amount"] == 500.0


def test_inventory_health(db):
    from datetime import date as _d
    from app.models.pellet import PelletDoseType, PelletLot, PelletStock
    dt_type = PelletDoseType(hormone="estradiol", dose_mg=12.5, label="Estradiol 12.5mg",
                             reorder_thresholds_by_location={"white_plains": 20})
    db.add(dt_type); db.flush()
    lot = PelletLot(dose_type_id=dt_type.id, qualgen_lot_number="QG-1",
                    expiration_date=_d(2026, 7, 1))   # expires within 90d of June 15
    db.add(lot); db.flush()
    db.add(PelletStock(lot_id=lot.id, location="white_plains", doses_on_hand=5, status="active"))
    db.commit()
    out = rpt.inventory_health(db, location=None, today=_d(2026, 6, 15))
    assert out["total_on_hand"] == 5
    assert out["by_location"]["white_plains"] == 5
    assert out["expiring_lots"] == 1                 # QG-1 expires 7/1, within 90d
    assert out["below_reorder"] == 1                 # 5 on hand < threshold 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_service.py -k "billing_backlog or inventory" -v`
Expected: FAIL — `AttributeError: ... 'billing_backlog'`.

- [ ] **Step 3: Append to `reports.py`**

```python
def billing_backlog(db: Session, *, location: Optional[str] = None,
                    provider: Optional[str] = None) -> dict:
    """Snapshot: inserted visits not yet billed."""
    rows = (_visit_base(db, location, provider)
            .filter(PelletVisit.status == "inserted",
                    PelletVisit.billed_at.is_(None)).all())
    total = round(sum(float(v.price_amount or 0) for v in rows), 2)
    return {"count": len(rows), "total_amount": total}


def inventory_health(db: Session, *, location: Optional[str] = None,
                     today: Optional[date] = None) -> dict:
    """Snapshot: on-hand doses per location, lots expiring <=90 days, and dose
    types below their reorder threshold."""
    from app.models.pellet import PelletDoseType, PelletLot, PelletStock
    from app.utils.dt import now_utc_naive
    today = today or now_utc_naive().date()
    horizon = today + timedelta(days=90)

    sq = db.query(PelletStock).filter(PelletStock.status == "active")
    if location:
        sq = sq.filter(PelletStock.location == location)
    stocks = sq.all()

    by_location: dict = {}
    expiring_lot_ids: set = set()
    onhand_by_loc_type: dict = {}   # (location, dose_type_id) -> doses
    lots = {l.id: l for l in db.query(PelletLot).all()}
    for s in stocks:
        by_location[s.location] = by_location.get(s.location, 0) + int(s.doses_on_hand or 0)
        lot = lots.get(s.lot_id)
        if lot and (s.doses_on_hand or 0) > 0 and lot.expiration_date <= horizon:
            expiring_lot_ids.add(s.lot_id)
        if lot:
            key = (s.location, lot.dose_type_id)
            onhand_by_loc_type[key] = onhand_by_loc_type.get(key, 0) + int(s.doses_on_hand or 0)

    # Below reorder: for each dose type with a per-location threshold, compare
    # that location's on-hand to the threshold.
    below = 0
    dose_types = {d.id: d for d in db.query(PelletDoseType).all()}
    for d in dose_types.values():
        thresholds = d.reorder_thresholds_by_location or {}
        for loc, thr in thresholds.items():
            if location and loc != location:
                continue
            if thr is None:
                continue
            on_hand = onhand_by_loc_type.get((loc, d.id), 0)
            if on_hand < int(thr):
                below += 1

    return {"total_on_hand": sum(by_location.values()),
            "by_location": by_location,
            "expiring_lots": len(expiring_lot_ids),
            "below_reorder": below}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/pellet/reports.py tests/test_pellet_reports_service.py
git commit -m "feat(pellet-reports): billing backlog + inventory health tiles"
```

---

### Task 4: Service — drill-down rows + CSV

**Files:**
- Modify: `backend/app/services/pellet/reports.py` (append)
- Test: `backend/tests/test_pellet_reports_service.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_rows_for_status_funnel_bucket(db):
    p = _patient(db)
    _visit(db, p, status="cancelled")
    _visit(db, p, status="cancelled")
    _visit(db, p, status="new")
    rows = rpt.rows_for(db, "status_funnel", date_from=date(2026, 6, 1),
                        date_to=date(2026, 6, 30), location=None, provider=None,
                        bucket="cancelled", today=date(2026, 6, 15))
    assert len(rows) == 2 and all(r["status"] == "cancelled" for r in rows)
    assert {"visit_id", "chart_number", "patient_name", "status"} <= set(rows[0])


def test_rows_to_csv_has_header_and_rows():
    csv_text = rpt.rows_to_csv([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    assert lines[0] == "a,b" and len(lines) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_service.py -k "rows" -v`
Expected: FAIL — `AttributeError: ... 'rows_for'`.

- [ ] **Step 3: Append to `reports.py`**

```python
import csv
import io


def _visit_row(v) -> dict:
    p = v.patient
    return {
        "visit_id": str(v.id),
        "chart_number": getattr(p, "chart_number", None),
        "patient_name": getattr(p, "patient_name", None),
        "scheduled_date": v.scheduled_date.strftime("%m/%d/%Y") if v.scheduled_date else None,
        "inserted_at": v.inserted_at.strftime("%m/%d/%Y") if v.inserted_at else None,
        "location": v.location,
        "provider": v.provider,
        "status": v.status,
        "visit_kind": v.visit_kind,
    }


def rows_for(db: Session, tile: str, *, date_from: date, date_to: date,
             location: Optional[str] = None, provider: Optional[str] = None,
             bucket: Optional[str] = None, today: Optional[date] = None) -> list[dict]:
    from app.utils.dt import now_utc_naive

    if tile == "status_funnel":
        q = _visit_base(db, location, provider)
        if bucket:
            q = q.filter(PelletVisit.status == bucket)
        return [_visit_row(v) for v in q.order_by(PelletVisit.scheduled_date).all()]

    if tile == "insertions":
        q = _inserted_in_range_q(db, date_from, date_to, location, provider)
        rows = q.all()
        if bucket:
            rows = [v for v in rows if (v.visit_kind or "unspecified") == bucket]
        out = []
        for v in rows:
            r = _visit_row(v)
            r["price_amount"] = float(v.price_amount or 0)
            out.append(r)
        return out

    if tile == "billing_backlog":
        rows = (_visit_base(db, location, provider)
                .filter(PelletVisit.status == "inserted",
                        PelletVisit.billed_at.is_(None)).all())
        out = []
        for v in rows:
            r = _visit_row(v)
            r["price_amount"] = float(v.price_amount or 0)
            out.append(r)
        return out

    if tile == "prerequisites":
        today = today or now_utc_naive().date()
        horizon = today + timedelta(days=14)
        q = (_visit_base(db, location, provider)
             .filter(PelletVisit.scheduled_date.isnot(None),
                     PelletVisit.scheduled_date >= today,
                     PelletVisit.scheduled_date <= horizon,
                     PelletVisit.status.in_(("new", "in_progress"))))
        out = []
        for v in q.order_by(PelletVisit.scheduled_date).all():
            p = v.patient
            if p is None:
                continue
            blockers = []
            if not _mammo_ok(db, p, today):
                blockers.append("mammo")
            if not _labs_ok(db, p, today):
                blockers.append("labs")
            if not _consent_ok(db, p.id):
                blockers.append("consent")
            if not blockers:
                continue
            if bucket and bucket not in blockers:
                continue
            r = _visit_row(v)
            r["blockers"] = blockers
            out.append(r)
        return out

    if tile == "recall_due":
        today = today or now_utc_naive().date()
        soon = today + timedelta(days=30)
        out = []
        patients = (db.query(PelletPatient)
                    .filter(PelletPatient.status == "active").all())
        for p in patients:
            visits = list(p.visits or [])
            if not visits:
                continue
            latest = max(visits, key=lambda v: (v.created_at or datetime.min))
            if location and latest.location != location:
                continue
            if provider and latest.provider != provider:
                continue
            dates = [d for d in (_effective_visit_date(v) for v in visits) if d]
            if not dates or _has_open_visit(visits):
                continue
            last_dt = max(dates)
            interval = p.recall_interval_months or 4
            due = last_dt + timedelta(days=interval * 30)
            kind = "overdue" if due < today else ("due_soon" if due <= soon else None)
            if kind is None:
                continue
            if bucket and bucket != kind:
                continue
            out.append({
                "patient_id": str(p.id),
                "chart_number": p.chart_number,
                "patient_name": p.patient_name,
                "last_inserted_at": last_dt.strftime("%m/%d/%Y"),
                "due_date": due.strftime("%m/%d/%Y"),
                "recall_interval_months": p.recall_interval_months or 4,
                "bucket": kind,
            })
        return out

    if tile == "inventory_health":
        from app.models.pellet import PelletDoseType, PelletLot, PelletStock
        sq = db.query(PelletStock).filter(PelletStock.status == "active",
                                          PelletStock.doses_on_hand > 0)
        if location:
            sq = sq.filter(PelletStock.location == location)
        lots = {l.id: l for l in db.query(PelletLot).all()}
        types = {d.id: d for d in db.query(PelletDoseType).all()}
        out = []
        for s in sq.all():
            lot = lots.get(s.lot_id)
            dose = types.get(lot.dose_type_id) if lot else None
            out.append({
                "location": s.location,
                "lot_number": lot.qualgen_lot_number if lot else None,
                "dose_type": (dose.label if dose else None),
                "doses_on_hand": int(s.doses_on_hand or 0),
                "expiration_date": (lot.expiration_date.strftime("%m/%d/%Y")
                                    if lot and lot.expiration_date else None),
            })
        return out

    raise ValueError(f"unknown tile: {tile}")


VALID_TILES = ("status_funnel", "insertions", "recall_due", "prerequisites",
               "billing_backlog", "inventory_health")


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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/pellet/reports.py tests/test_pellet_reports_service.py
git commit -m "feat(pellet-reports): drill-down rows + CSV serialization"
```

---

### Task 5: Router + registration

**Files:**
- Create: `backend/app/routers/pellet_reports.py`
- Modify: `backend/app/main.py` (import + include_router)
- Test: `backend/tests/test_pellet_reports_router.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_reports_router.py
"""Pellet Reports endpoints. `client` is the super-admin fixture."""
from datetime import datetime

from app.models.pellet import PelletPatient, PelletVisit


def _seed(db, **kw):
    # chart_number is unique — derive a fresh one per call.
    n = db.query(PelletPatient).count()
    p = PelletPatient(chart_number=f"PC{n + 1}", patient_name="Doe, J", status="active")
    db.add(p); db.commit(); db.refresh(p)
    base = dict(patient_id=p.id, visit_kind="initial", status="cancelled",
                location="white_plains", provider="Cooke, Aryian, MD")
    base.update(kw)
    v = PelletVisit(**base); db.add(v); db.commit(); db.refresh(v)
    return p, v


def test_summary_returns_all_tiles(client, db):
    _seed(db, status="cancelled")
    _seed(db, status="inserted", inserted_at=datetime(2026, 6, 10))
    r = client.get("/api/pellets/reports/summary?from=2026-06-01&to=2026-06-30")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("status_funnel", "insertions", "recall_due", "prerequisites",
                "billing_backlog", "inventory_health", "period", "providers"):
        assert key in body
    assert body["status_funnel"]["by_status"]["cancelled"] == 1


def test_rows_json_and_csv(client, db):
    _seed(db, status="cancelled")
    _seed(db, status="cancelled")
    j = client.get("/api/pellets/reports/status_funnel/rows?bucket=cancelled")
    assert j.status_code == 200 and len(j.json()["items"]) == 2
    c = client.get("/api/pellets/reports/status_funnel/rows?bucket=cancelled&format=csv")
    assert c.status_code == 200
    assert c.headers["content-type"].startswith("text/csv")
    assert c.text.splitlines()[0].startswith("visit_id")


def test_rows_unknown_tile_404(client, db):
    assert client.get("/api/pellets/reports/bogus/rows").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_router.py -v`
Expected: FAIL — 404 on `/summary` (router not mounted).

- [ ] **Step 3: Create the router**

```python
# backend/app/routers/pellet_reports.py
"""Pellet Reports endpoints: a one-shot summary of all tiles, plus per-tile
drill-down rows (JSON or CSV). Read-only (Tier.VIEW)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services.pellet import reports as rpt
from app.utils.dt import now_utc_naive

router = APIRouter(prefix="/pellets/reports", tags=["pellet-reports"])


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
    provider: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
):
    df, dt = _parse_range(from_, to)
    return {
        "period": {"from": df.isoformat(), "to": dt.isoformat()},
        "providers": rpt.providers(db),
        "status_funnel": rpt.status_funnel(db, location=location, provider=provider),
        "insertions": _isoize(rpt.insertions(db, date_from=df, date_to=dt,
                                             location=location, provider=provider)),
        "recall_due": rpt.recall_due(db, location=location, provider=provider),
        "prerequisites": rpt.prerequisites(db, location=location, provider=provider),
        "billing_backlog": rpt.billing_backlog(db, location=location, provider=provider),
        "inventory_health": rpt.inventory_health(db, location=location),
    }


@router.get("/{tile}/rows")
def reports_rows(
    tile: str,
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    location: Optional[str] = None,
    provider: Optional[str] = None,
    bucket: Optional[str] = None,
    output_format: Optional[str] = Query(None, alias="format"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
):
    if tile not in rpt.VALID_TILES:
        raise HTTPException(status_code=404, detail="unknown report tile")
    df, dt = _parse_range(from_, to)
    rows = rpt.rows_for(db, tile, date_from=df, date_to=dt, location=location,
                        provider=provider, bucket=bucket)
    if (output_format or "").lower() == "csv":
        csv_text = rpt.rows_to_csv(rows)
        filename = f"pellet-{tile}-{df.isoformat()}_{dt.isoformat()}.csv"
        return StreamingResponse(
            iter([csv_text]), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return {"items": rows}
```

- [ ] **Step 4: Register the router in `main.py`**

In `backend/app/main.py`, add `pellet_reports` to the `from app.routers import ...` line, then add an include next to the existing pellet router include (search `include_router(pellet.router` or `pellet`). Mirror the EXACT form used for the `pellet` router (read the surrounding lines):

```python
app.include_router(pellet_reports.router, prefix="/api")
```

If the `pellet` router include passes `dependencies=[Depends(requires_tier(Module.PELLETS, Tier.VIEW))]`, match that form instead (the endpoints also self-gate, which is harmless, but follow the established pattern — prefer ONE gate; if neighbors gate at include-level, gate there and you may drop the per-endpoint deps, but the simplest correct choice is to keep just the `prefix="/api"` include since each endpoint already declares `requires_tier`).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_router.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/routers/pellet_reports.py app/main.py tests/test_pellet_reports_router.py
git commit -m "feat(pellet-reports): summary + drill-down rows/CSV endpoints"
```

---

### Task 6: Frontend — Reports page + route + nav

**Files:**
- Create: `frontend/src/pages/PelletReports.jsx`
- Modify: `frontend/src/routes.jsx`, `frontend/src/components/pellet/PelletNav.jsx`

- [ ] **Step 1: Inspect the patterns to mirror**

Run from `frontend/`:
- `grep -n "PelletActivity\|path: 'activity'\|M.PELLETS" src/routes.jsx | head`
- `grep -n "to:\|label:\|LINKS" src/components/pellet/PelletNav.jsx | head`
- Read `src/pages/SurgeryReports.jsx` fully (the reference implementation: filter bar, tile grid, drill-down modal, blob CSV download) and adapt it for pellets.

- [ ] **Step 2: Add the route (routes.jsx)**

Add the import next to the other pellet page imports:
```javascript
import PelletReports from './pages/PelletReports'
```
Add a child route under the `/pellets` `PelletNav` route (next to `activity`/`audit`):
```javascript
    { path: 'reports',      element: <PelletReports />,       module: M.PELLETS, tier: TIER.VIEW },
```

- [ ] **Step 3: Add the nav link (PelletNav.jsx)**

In the `LINKS` array, add (e.g. after the Audit entry):
```javascript
    { to: '/pellets/reports',   label: 'Reports',   tier: TIER.VIEW },
```

- [ ] **Step 4: Create `src/pages/PelletReports.jsx`**

Adapt `SurgeryReports.jsx`. Match its `api`/`fmt` imports, page shell, styling, the date-preset logic (This Month / Last Month / Last 30 / 90 / Custom → `from`/`to` ISO), the 6-tile responsive grid, the drill-down modal, and the blob CSV download (`api.get(..., {responseType:'blob'})`). Pellet specifics:
- Endpoint base: `/pellets/reports`. Summary `useQuery(['pellet-report-summary', from, to, location, provider], () => api.get('/pellets/reports/summary', { params: { from, to, location: location||undefined, provider: provider||undefined } }).then(r => r.data))`.
- Filters: date preset; **location** select (`''`=All, `white_plains`=White Plains, `brandywine`=Brandywine, `arlington`=Arlington); **provider** select (`''`=All + options from `summary.providers` — read them off the loaded summary, or keep a separate light query; reading `data?.providers` is fine since providers is range-independent).
- Status labels for the funnel (local map): `new`='New', `in_progress`='In Progress', `inserted`='Inserted', `billed`='Billed', `cancelled`='Cancelled', `rescheduled`='Rescheduled'. Order: new, in_progress, inserted, billed, rescheduled, cancelled.
- 6 tiles reading the summary payload:
  1. **Visit Status Funnel** — `status_funnel.by_status` rows (label: count), clickable → `bucket=<status>`.
  2. **Insertions** — `insertions.total` headline; `by_kind` split (Initial/Booster/Repeat); delta vs prior (`insertions.delta`).
  3. **Recall Due** — `recall_due.overdue` + `recall_due.due_soon` (clickable to `bucket=overdue`/`due_soon`); show `total`.
  4. **Prerequisites Not Ready** — `prerequisites.total` headline; blocker chips Mammo/Labs/Consent from `by_blocker` (skip zeros), clickable → `bucket=<blocker>`.
  5. **Billing Backlog** — `billing_backlog.count` + `fmt.currency(billing_backlog.total_amount)`.
  6. **Inventory Health** — `inventory_health.total_on_hand` headline; per-location rows from `by_location` (clickable → `bucket=<location>`); `expiring_lots` and `below_reorder` counts shown as chips.
- Drill-down + Download CSV identical in shape to SurgeryReports (call `/pellets/reports/{tile}/rows` with params + `bucket`, render `items` table, CSV via blob). Dates MM/DD/YYYY via `fmt.date`, money `$X.XX` via `fmt.currency`. Title Case titles + buttons.

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: succeeds, no errors referencing the new/changed files.

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/pages/PelletReports.jsx src/routes.jsx src/components/pellet/PelletNav.jsx
git commit -m "feat(pellet-reports): Reports page with filters, tiles, drill-down + CSV"
```

---

### Task 7: Authenticated walk-through

**Files:**
- Create: `backend/tests/test_pellet_reports_walkthrough.py`

- [ ] **Step 1: Write the walk-through test**

```python
# backend/tests/test_pellet_reports_walkthrough.py
"""Authenticated walk-through of Pellet Reports: seed a small set, read the
summary, drill into a tile, and export CSV. `client` is the super-admin fixture."""
from datetime import datetime

from app.models.pellet import PelletPatient, PelletVisit


def _seed(db, **kw):
    p = PelletPatient(chart_number=f"PC-{kw.get('status','x')}",
                      patient_name="Roe, Pat", status="active")
    db.add(p); db.commit(); db.refresh(p)
    base = dict(patient_id=p.id, visit_kind="initial", status="new",
                location="white_plains", provider="Cooke, Aryian, MD")
    base.update(kw)
    v = PelletVisit(**base); db.add(v); db.commit(); db.refresh(v)
    return p, v


def test_pellet_reports_walkthrough(client, db, capsys):
    log = []
    _seed(db, status="cancelled")
    _seed(db, status="new")
    _seed(db, status="inserted", visit_kind="booster", inserted_at=datetime(2026, 6, 10))

    body = client.get("/api/pellets/reports/summary?from=2026-06-01&to=2026-06-30").json()
    assert set(body) >= {"status_funnel", "insertions", "recall_due", "prerequisites",
                         "billing_backlog", "inventory_health", "period", "providers"}
    log.append(f"1. /summary -> funnel {body['status_funnel']['by_status']}, "
               f"insertions {body['insertions']['total']} (delta {body['insertions']['delta']})")

    items = client.get("/api/pellets/reports/status_funnel/rows?bucket=cancelled").json()["items"]
    assert len(items) == 1 and items[0]["status"] == "cancelled"
    log.append(f"2. drill status_funnel?bucket=cancelled -> {len(items)} visit")

    csv_resp = client.get("/api/pellets/reports/status_funnel/rows?bucket=cancelled&format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.text.splitlines()[0].startswith("visit_id")
    log.append("3. CSV export -> text/csv with header + 1 row")

    with capsys.disabled():
        print("\n  -- Pellet Reports walk-through (authenticated) --")
        for line in log:
            print("   " + line)
```

- [ ] **Step 2: Run it**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_reports_walkthrough.py -v -s`
Expected: PASS, log prints.

- [ ] **Step 3: Commit**

```bash
cd backend && git add tests/test_pellet_reports_walkthrough.py
git commit -m "test(pellet-reports): authenticated reports walk-through"
```

---

## Final Verification (after all tasks)
- [ ] `cd backend && ./venv/bin/python -m pytest tests/ -k "pellet_reports" -v` → all PASS.
- [ ] `cd frontend && npm run build` → clean.
- [ ] No new failures vs baseline: `cd backend && ./venv/bin/python -m pytest tests/ -k "pellet" -q`.

## Notes for the implementer
- **No new tables/columns** — compute on request.
- **Recall Due mirrors `pellet.py` `recall_is_due`** (effective date = inserted_at.date() else scheduled_date; `interval*30` days; excluded when the patient has an open visit). Keep that math identical so the tile matches the patient roster's "Recall Due".
- **`is_historical` visits** are excluded from `_visit_base` (so funnel/insertions/prereqs/billing ignore them) but Recall Due loads `p.visits` directly, so a historical past insertion still counts as the last visit for recall timing — intentional.
- **Snapshot vs period:** status_funnel/recall_due/prerequisites/billing_backlog/inventory_health ignore the date range; only `insertions` uses it. location/provider apply to all visit-based tiles; inventory takes location only.
