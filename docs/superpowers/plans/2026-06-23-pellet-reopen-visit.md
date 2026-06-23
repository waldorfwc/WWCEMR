# Reopen Pellet Visit (+ Missing-Lot Flag) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a manager reopen a completed/cancelled pellet visit, correct its doses/lots with inventory kept accurate, then close it back; plus a flag that surfaces real `inserted`/`billed` visits missing a lot.

**Architecture:** Reopen flips a completed visit to `in_progress` and stamps tracking columns; a dedicated MANAGE-gated dose-correction endpoint reconciles stock the same way the live flow does (return old lot, pull new — skipped for `is_historical` visits); close-reopen finalizes dangling doses and returns the visit to its prior completed status (`billed` stays `billed`). A computed `missing_lot` flag drives a dashboard view + per-visit badge.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React + @tanstack/react-query + axios (frontend), pytest.

**Spec:** `docs/superpowers/specs/2026-06-23-pellet-reopen-visit-design.md`

---

## Verified existing facts

- `PelletVisit` (`backend/app/models/pellet.py`): `status` ∈ `new|in_progress|inserted|billed|cancelled|rescheduled`; `is_historical` (Boolean, default False); `inserted_at/by`, `scheduled_date`, `location`, `outcome`, `billed_at/by`. `PelletVisitDose`: `lot_id` (nullable FK), `quantity`, `position`, `status` ∈ `planned|pulled|added|inserted|reduced|returned|disposed`, `resolved_at/by`. `PelletStock.doses_on_hand`.
- Router `backend/app/routers/pellet.py`: `router = APIRouter(prefix="/pellets", ...)` (mounted under `/api`). Inline helpers: `_audit(db, *, actor, action, dose_type_id=None, lot_id=None, ..., location=None, delta_doses=None, summary=None, detail=None)`; `_adjust_stock(db, stock, delta)` (delta>0 increment, delta<0 conditional decrement → 409 if insufficient); `_specific_lot_with_stock(db, lot_id, dose_type_id, qty, location)` (raises 422/409, returns `(lot, stock)`); `_earliest_lot_with_stock(...)`; `_get_or_create_stock(db, lot_id, location)`; `_visit_dict(v, include_milestones=True, include_doses=True)`; `_is_admin(db, current_user)`. Auth: `requires_tier(Module.PELLETS, Tier.MANAGE)` / `Tier.WORK`. Pydantic models defined inline in the router. `now_utc_naive` imported.
- `append_dose` (`POST /pellets/visits/{id}/doses/...append`) branches on `is_confirmed_visit = v.status in ("inserted","billed")`: confirmed → create dose `status="inserted"`, **no stock**; else → pull lot (`_specific_lot_with_stock` or FIFO) + `_adjust_stock(-qty)` + dose `status="planned"`.
- The bag-fill return path already guards: `if (not v.is_historical) and d.lot_id and location: _adjust_stock(db, _get_or_create_stock(db, d.lot_id, location), d.quantity)`.
- Lightweight migrations: `backend/app/database.py` has a `needed` list of `(table, column, coltype)`; an idiom that skips existing columns and runs `ALTER TABLE ... ADD COLUMN`, with `_adapt_coltype_for_dialect` mapping `DATETIME→TIMESTAMP` and `BOOLEAN DEFAULT 0→FALSE` for Postgres.
- Patient list `GET /pellets/patients` (params incl. `view` ∈ `PATIENT_VIEWS`, `location`, `from_date`, `to_date`, ...); counts `GET /pellets/patient-view-counts` returns `{view: count}`; `PATIENT_VIEWS = ["roster","last_visits","upcoming","recall_due","needs_mammo","needs_dosing","ready","paid","unpaid"]`.
- Frontend: `frontend/src/pages/PelletPatientDetail.jsx` (patient-centric; visits shown as cards). `import api from '../utils/api'` ... `useQuery({queryKey:['pellet-patient', id], queryFn: () => api.get(`/pellets/patients/${id}`)...})`; mutations invalidate `['pellet-patient', id]` + `['pellet-patient-counts']`. `MODULE.PELLETS` + `TIER.MANAGE` from `routes.jsx`.
- Tests: `backend/tests/test_pellet_*.py` seed `PelletPatient`/`PelletVisit`/`PelletVisitDose`/`PelletLot`/`PelletStock` directly on `db`; stock asserted via `db.query(PelletStock).filter(...).first().doses_on_hand`. Use `client_factory(user=u)` with a MANAGE/super-admin `User`.

---

## Task 1: Migration + serializer fields + missing-lot computation

**Files:**
- Modify: `backend/app/models/pellet.py` (4 columns on `PelletVisit`)
- Modify: `backend/app/database.py` (lightweight migration entries)
- Modify: `backend/app/routers/pellet.py` (`_visit_missing_lot` helper + `_visit_dict` fields)
- Test: `backend/tests/test_pellet_reopen_visit.py` (create)

- [ ] **Step 1: Add the model columns**

In `backend/app/models/pellet.py`, in the `PelletVisit` class after `is_historical`:

```python
    # Reopen tracking — a manager can reopen a completed/cancelled visit to
    # correct it; these record the open state and what status to return to.
    reopened_at       = Column(DateTime, nullable=True)
    reopened_by       = Column(String(120), nullable=True)
    reopened_reason   = Column(Text, nullable=True)
    pre_reopen_status = Column(String(20), nullable=True)
```

- [ ] **Step 2: Add the lightweight-migration entries**

In `backend/app/database.py`, add to the `needed` list (the `(table, column, coltype)` tuples):

```python
    ("pellet_visits", "reopened_at", "DATETIME"),
    ("pellet_visits", "reopened_by", "VARCHAR(120)"),
    ("pellet_visits", "reopened_reason", "TEXT"),
    ("pellet_visits", "pre_reopen_status", "VARCHAR(20)"),
```

- [ ] **Step 3: Write the failing test for the missing-lot computation + serializer**

Create `backend/tests/test_pellet_reopen_visit.py`:

```python
from datetime import date
from app.models.user import User
from app.models.pellet import (
    PelletPatient, PelletVisit, PelletVisitDose, PelletDoseType,
    PelletLot, PelletStock,
)
from app.routers.pellet import _visit_missing_lot


def _mgr(db):
    u = User(email="mgr@waldorfwomenscare.com", display_name="Mgr", is_super_admin=True)
    db.add(u); db.commit()
    return u


def _patient(db):
    p = PelletPatient(patient_name="Tober, Catrina", chart_number="14943",
                      patient_dob=date(1975, 3, 2))
    db.add(p); db.commit(); db.refresh(p)
    return p


def _dose_type(db):
    dt = PelletDoseType(label="Testosterone 200mg", is_controlled=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _lot(db, dt, qty=10, loc="white_plains", number="LOT-A"):
    lot = PelletLot(dose_type_id=dt.id, qualgen_lot_number=number,
                    expiration_date=date(2027, 1, 1))
    db.add(lot); db.flush()
    db.add(PelletStock(lot_id=lot.id, location=loc, doses_on_hand=qty, status="active"))
    db.commit(); db.refresh(lot)
    return lot


def _visit(db, p, status="inserted", historical=False, location="white_plains"):
    v = PelletVisit(patient_id=p.id, visit_kind="initial", status=status,
                    location=location, is_historical=historical,
                    scheduled_date=date(2026, 6, 5))
    db.add(v); db.commit(); db.refresh(v)
    return v


def test_missing_lot_true_when_zero_doses(db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    assert _visit_missing_lot(v) is True


def test_missing_lot_true_when_a_dose_has_no_lot(db):
    p = _patient(db); dt = _dose_type(db); v = _visit(db, p, status="billed")
    db.add(PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2,
                           position=1, status="inserted", lot_id=None))
    db.commit(); db.refresh(v)
    assert _visit_missing_lot(v) is True


def test_missing_lot_false_when_all_doses_lotted(db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt); v = _visit(db, p, status="inserted")
    db.add(PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2,
                           position=1, status="inserted", lot_id=lot.id))
    db.commit(); db.refresh(v)
    assert _visit_missing_lot(v) is False


def test_missing_lot_false_for_historical(db):
    p = _patient(db); v = _visit(db, p, status="inserted", historical=True)
    assert _visit_missing_lot(v) is False


def test_missing_lot_false_for_non_completed(db):
    p = _patient(db); v = _visit(db, p, status="in_progress")
    assert _visit_missing_lot(v) is False
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd backend && pytest tests/test_pellet_reopen_visit.py -q`
Expected: FAIL — `ImportError: cannot import name '_visit_missing_lot'`.

- [ ] **Step 5: Implement `_visit_missing_lot` + serializer fields**

In `backend/app/routers/pellet.py`, add near `_visit_dict`:

```python
def _visit_missing_lot(v) -> bool:
    """A real, completed visit that lacks lot data — zero dose rows or any
    dose without a lot. Historical backfills are excluded (knowingly
    incomplete, not a data error)."""
    if v.is_historical or v.status not in ("inserted", "billed"):
        return False
    doses = v.doses or []
    if not doses:
        return True
    return any(d.lot_id is None for d in doses)
```

In `_visit_dict`, add to the `out` dict (alongside `is_historical`):

```python
        "reopened_at":       v.reopened_at.isoformat() if v.reopened_at else None,
        "reopened_by":       v.reopened_by,
        "reopened_reason":   v.reopened_reason,
        "pre_reopen_status": v.pre_reopen_status,
        "missing_lot":       _visit_missing_lot(v),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_pellet_reopen_visit.py -q`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/pellet.py backend/app/database.py backend/app/routers/pellet.py backend/tests/test_pellet_reopen_visit.py
git commit -m "feat(pellet): reopen-tracking columns + missing-lot computation"
```

---

## Task 2: Reopen + close-reopen endpoints

**Files:**
- Modify: `backend/app/routers/pellet.py`
- Test: `backend/tests/test_pellet_reopen_visit.py`

- [ ] **Step 1: Append failing tests**

```python
from unittest.mock import ANY


def _client(client_factory, db):
    return client_factory(user=_mgr(db))


def test_reopen_inserted_visit_flips_to_in_progress(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "missing lot"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_progress"
    assert body["pre_reopen_status"] == "inserted"
    assert body["reopened_by"] and body["reopened_reason"] == "missing lot"


def test_reopen_rejects_non_completed(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="in_progress")
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "x"})
    assert r.status_code == 409


def test_reopen_requires_reason(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "  "})
    assert r.status_code == 422


def test_reopen_twice_409(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "a"})
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "b"})
    assert r.status_code == 409


def test_close_reopen_billed_returns_to_billed(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="billed")
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "fix"})
    r = client.post(f"/api/pellets/visits/{v.id}/close-reopen")
    assert r.status_code == 200
    assert r.json()["status"] == "billed"
    assert r.json()["reopened_at"] is None


def test_close_reopen_inserted_returns_to_inserted(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "fix"})
    r = client.post(f"/api/pellets/visits/{v.id}/close-reopen")
    assert r.json()["status"] == "inserted"


def test_close_reopen_from_cancelled_goes_inserted(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="cancelled")
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "un-cancel"})
    r = client.post(f"/api/pellets/visits/{v.id}/close-reopen")
    assert r.json()["status"] == "inserted"


def test_close_reopen_not_reopened_409(client_factory, db):
    p = _patient(db); v = _visit(db, p, status="inserted")
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/close-reopen")
    assert r.status_code == 409
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && pytest tests/test_pellet_reopen_visit.py -k reopen -q`
Expected: FAIL — routes 404.

- [ ] **Step 3: Implement a visit loader + the two endpoints**

In `backend/app/routers/pellet.py` (near the other visit endpoints). If a `_load_visit` helper already exists, reuse it; otherwise add:

```python
def _load_visit(db, visit_id: str):
    from sqlalchemy.orm import joinedload
    v = (db.query(PelletVisit)
           .options(joinedload(PelletVisit.doses))
           .filter(PelletVisit.id == visit_id).first())
    if v is None:
        raise HTTPException(status_code=404, detail="visit not found")
    return v


_REOPENABLE_STATUSES = {"inserted", "billed", "cancelled"}


class ReopenIn(BaseModel):
    reason: str


@router.post("/visits/{visit_id}/reopen")
def reopen_visit(visit_id: str, payload: ReopenIn,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    """Reopen a completed/cancelled visit so a manager can correct it. Flips
    status to in_progress and records what to return to on close."""
    v = _load_visit(db, visit_id)
    if v.reopened_at is not None:
        raise HTTPException(status_code=409, detail="visit is already reopened")
    if v.status not in _REOPENABLE_STATUSES:
        raise HTTPException(status_code=409,
                            detail=f"cannot reopen a visit in status {v.status}")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")
    by = current_user.get("email") or "system"
    v.pre_reopen_status = v.status
    v.reopened_at = now_utc_naive()
    v.reopened_by = by
    v.reopened_reason = reason
    v.status = "in_progress"
    _audit(db, actor=by, action="visit_reopened",
           summary=f"Reopened visit (was {v.pre_reopen_status})",
           detail={"visit_id": str(v.id), "pre_status": v.pre_reopen_status,
                   "reason": reason})
    db.commit(); db.refresh(v)
    return _visit_dict(v)


@router.post("/visits/{visit_id}/close-reopen")
def close_reopen(visit_id: str,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    """Finish a reopen: finalize any dangling doses to inserted and return the
    visit to its prior completed status (billed stays billed; everything else,
    including reopened-from-cancelled, completes to inserted)."""
    v = _load_visit(db, visit_id)
    if v.reopened_at is None:
        raise HTTPException(status_code=409, detail="visit is not reopened")
    by = current_user.get("email") or "system"
    now = now_utc_naive()
    target = "billed" if v.pre_reopen_status == "billed" else "inserted"
    for d in (v.doses or []):
        if d.status in ("planned", "pulled", "added"):
            d.status = "inserted"
            d.resolved_at = d.resolved_at or (v.inserted_at or now)
            d.resolved_by = d.resolved_by or by
    if target == "inserted" and v.inserted_at is None:
        v.inserted_at = now
        v.inserted_by = by
    prior = v.pre_reopen_status
    v.status = target
    v.reopened_at = None
    v.reopened_by = None
    v.reopened_reason = None
    v.pre_reopen_status = None
    _audit(db, actor=by, action="visit_reopen_closed",
           summary=f"Closed reopen → {target}",
           detail={"visit_id": str(v.id), "from_reopen_of": prior, "to": target})
    db.commit(); db.refresh(v)
    return _visit_dict(v)
```

(Implementer: confirm `BaseModel`, `Session`, `Depends`, `get_db`, `HTTPException`, `now_utc_naive`, `Module`, `Tier`, `requires_tier`, `joinedload` are imported in this file — they are used elsewhere here.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_pellet_reopen_visit.py -k reopen -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/pellet.py backend/tests/test_pellet_reopen_visit.py
git commit -m "feat(pellet): reopen + close-reopen visit endpoints (MANAGE)"
```

---

## Task 3: Dose-correction endpoint + is_historical stock guard

**Files:**
- Modify: `backend/app/routers/pellet.py`
- Test: `backend/tests/test_pellet_reopen_visit.py`

This is the inventory-reconciliation core. The dose-correction endpoint returns the dose's old lot to stock and pulls the new one — skipped entirely for `is_historical` visits.

- [ ] **Step 1: Append failing tests**

```python
def _stock(db, lot, loc="white_plains"):
    return (db.query(PelletStock)
              .filter(PelletStock.lot_id == lot.id, PelletStock.location == loc)
              .first())


def test_correct_dose_binds_lot_and_decrements_stock(client_factory, db):
    # real inserted visit, dose with NO lot (the Catrina case)
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p, status="inserted")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="inserted", lot_id=None)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "lot"})
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot.id)})
    assert r.status_code == 200
    assert _stock(db, lot).doses_on_hand == 7   # 10 - 3 pulled


def test_correct_dose_swap_returns_old_and_pulls_new(client_factory, db):
    p = _patient(db); dt = _dose_type(db)
    lot_a = _lot(db, dt, qty=5, number="A")
    lot_b = _lot(db, dt, qty=5, number="B")
    v = _visit(db, p, status="inserted")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2,
                        position=1, status="inserted", lot_id=lot_a.id)
    db.add(d)
    # reflect that lot_a already gave 2 doses
    _stock(db, lot_a).doses_on_hand = 3
    db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "swap"})
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot_b.id)})
    assert r.status_code == 200
    assert _stock(db, lot_a).doses_on_hand == 5   # 3 + 2 returned
    assert _stock(db, lot_b).doses_on_hand == 3   # 5 - 2 pulled


def test_correct_dose_historical_is_stock_neutral(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p, status="inserted", historical=True)
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="inserted", lot_id=None)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "lot"})
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot.id)})
    assert r.status_code == 200
    assert _stock(db, lot).doses_on_hand == 10   # unchanged — historical
    db.refresh(d); assert str(d.lot_id) == str(lot.id)  # but lot recorded


def test_correct_dose_requires_reopened(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt)
    v = _visit(db, p, status="inserted")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=1,
                        position=1, status="inserted", lot_id=None)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    r = client.patch(f"/api/pellets/visits/{v.id}/doses/{d.id}",
                     json={"lot_id": str(lot.id)})
    assert r.status_code == 409  # not reopened
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && pytest tests/test_pellet_reopen_visit.py -k correct_dose -q`
Expected: FAIL — route 404.

- [ ] **Step 3: Implement the dose-correction endpoint**

In `backend/app/routers/pellet.py`:

```python
class DoseCorrectIn(BaseModel):
    lot_id: Optional[str] = None
    quantity: Optional[int] = None
    dose_type_id: Optional[str] = None


@router.patch("/visits/{visit_id}/doses/{dose_id}")
def correct_dose(visit_id: str, dose_id: str, payload: DoseCorrectIn,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.MANAGE))):
    """Correct one dose on a reopened visit (bind/change lot, quantity, or dose
    type). Reconciles stock with the same rules as the live flow: return the
    old lot, pull the new one — skipped entirely for historical visits."""
    v = _load_visit(db, visit_id)
    if v.reopened_at is None:
        raise HTTPException(status_code=409, detail="visit must be reopened to correct a dose")
    d = next((x for x in (v.doses or []) if str(x.id) == dose_id), None)
    if d is None:
        raise HTTPException(status_code=404, detail="dose not found")
    by = current_user.get("email") or "system"
    location = v.location

    new_qty = payload.quantity if payload.quantity is not None else d.quantity
    if new_qty is None or new_qty < 1:
        raise HTTPException(status_code=422, detail="quantity must be >= 1")
    new_lot = payload.lot_id if payload.lot_id is not None else (str(d.lot_id) if d.lot_id else None)
    new_dt = payload.dose_type_id or str(d.dose_type_id)

    if not v.is_historical:
        if new_lot and not location:
            raise HTTPException(status_code=409,
                                detail="visit has no location — cannot pull from inventory")
        # Return the old lot's doses to stock.
        if d.lot_id and location:
            old_stock = _get_or_create_stock(db, d.lot_id, location)
            _adjust_stock(db, old_stock, d.quantity)
        # Pull the new lot (validates availability; raises 409/422 on failure).
        if new_lot:
            lot, stock = _specific_lot_with_stock(db, new_lot, new_dt, new_qty, location)
            _adjust_stock(db, stock, -(new_qty))

    d.lot_id = new_lot
    d.quantity = new_qty
    d.dose_type_id = new_dt
    d.resolved_at = d.resolved_at or now_utc_naive()
    d.resolved_by = by
    _audit(db, actor=by, action="dose_corrected", lot_id=new_lot, location=location,
           summary="Corrected dose on reopened visit",
           detail={"visit_id": str(v.id), "dose_id": str(d.id),
                   "new_lot_id": new_lot, "new_qty": new_qty})
    db.commit(); db.refresh(v)
    return _visit_dict(v)
```

(Note: `_adjust_stock` already rolls back via 409 if the new lot lacks stock; because the return-old happens first in the same transaction, a failed pull aborts the whole correction. `Optional` is already imported.)

- [ ] **Step 4: Add the `is_historical` guard to `append_dose`**

So that *adding* a dose to a reopened historical visit is also stock-neutral (a reopened historical visit has `status="in_progress"`, which would otherwise hit the stock-pulling branch). In `append_dose`, change the branch condition from `if is_confirmed_visit:` to also treat historical as stock-neutral:

```python
    stock_neutral = is_confirmed_visit or v.is_historical
    if stock_neutral:
        # historical / confirmed manager-edit — no stock impact
        d = PelletVisitDose(... status="inserted", resolved_at=..., resolved_by=by, ...)
        db.add(d); db.flush()
        # ... existing audit ...
    else:
        # ... existing pull-lot + _adjust_stock(-qty) + status="planned" branch ...
```

(Implementer: make the minimal edit — introduce `stock_neutral` and use it where `is_confirmed_visit` currently gates the no-stock path. Do not change the live non-historical behavior.)

- [ ] **Step 5: Run tests**

Run: `cd backend && pytest tests/test_pellet_reopen_visit.py -q` → all pass.
Then regression: `cd backend && pytest tests/ -k pellet -q` → must stay green (live flow unchanged).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/pellet.py backend/tests/test_pellet_reopen_visit.py
git commit -m "feat(pellet): dose-correction endpoint + historical stock guard"
```

---

## Task 4: Missing-lot query + dashboard view + list filter

**Files:**
- Modify: `backend/app/routers/pellet.py`
- Test: `backend/tests/test_pellet_reopen_visit.py`

- [ ] **Step 1: Append failing tests**

```python
def test_missing_lot_count_and_view(client_factory, db):
    p1 = _patient(db)
    v1 = _visit(db, p1, status="inserted")  # zero doses → missing
    p2 = PelletPatient(patient_name="Ok, Pat", chart_number="222", patient_dob=date(1980,1,1))
    db.add(p2); db.commit(); db.refresh(p2)
    dt = _dose_type(db); lot = _lot(db, dt)
    v2 = _visit(db, p2, status="inserted")
    db.add(PelletVisitDose(visit_id=v2.id, dose_type_id=dt.id, quantity=1,
                           position=1, status="inserted", lot_id=lot.id))
    db.commit()
    client = _client(client_factory, db)
    counts = client.get("/api/pellets/patient-view-counts").json()
    assert counts["missing_lot"] == 1
    lst = client.get("/api/pellets/patients?view=missing_lot").json()
    names = [row["patient_name"] for row in (lst if isinstance(lst, list) else lst.get("items", []))]
    assert "Tober, Catrina" in names and "Ok, Pat" not in names
```

(Implementer: match the real shape of the `/patients` list response — it may be a bare list or `{items: [...]}`; the test above tolerates both.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_pellet_reopen_visit.py -k missing_lot_count -q`
Expected: FAIL — `missing_lot` not in counts / unknown view 422.

- [ ] **Step 3: Implement the query helper + wire view & count**

Add the helper:

```python
def _patient_has_missing_lot(p) -> bool:
    return any(_visit_missing_lot(v) for v in (p.visits or []))
```

In `PATIENT_VIEWS`, add `"missing_lot"`:

```python
PATIENT_VIEWS = ["roster", "last_visits", "upcoming", "recall_due",
                 "needs_mammo", "needs_dosing", "ready", "paid", "unpaid",
                 "missing_lot"]
```

In `patient_view_counts`, initialize and count it (the loop already iterates patients with `.visits.doses` joined):

```python
    out["missing_lot"] = 0
    # inside the per-patient loop:
        if _patient_has_missing_lot(p):
            out["missing_lot"] += 1
```

In `list_patients`, handle the `missing_lot` view by filtering the patient set to those with a missing-lot visit (apply in the same place other views filter; since views are computed in Python here, filter the result list):

```python
    if view == "missing_lot":
        patients = [p for p in patients if _patient_has_missing_lot(p)]
```

(Implementer: insert this where the function already narrows `patients` per `view`; reuse the existing eager-load of `visits`→`doses`. Keep pagination behavior intact.)

- [ ] **Step 4: Run tests**

Run: `cd backend && pytest tests/test_pellet_reopen_visit.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/pellet.py backend/tests/test_pellet_reopen_visit.py
git commit -m "feat(pellet): missing-lot dashboard view + count"
```

---

## Task 5: Frontend — reopen/close + dose correction on the visit card

**Files:**
- Modify: `frontend/src/pages/PelletPatientDetail.jsx`

- [ ] **Step 1: Read the visit-card section**

Open `frontend/src/pages/PelletPatientDetail.jsx` and locate the component that renders a single visit card (status, doses, existing action buttons) and the `useQueryClient`/`api` usage. Identify the dose-row rendering and where lot is shown (`dose.qualgen_lot` / `dose.lot_id`).

- [ ] **Step 2: Add reopen/close controls + banner**

For a visit whose `status` ∈ {`inserted`,`billed`,`cancelled`} and where the user has MANAGE (`tier(MODULE.PELLETS, TIER.MANAGE)` — use the same tier helper other gated controls in this file use), render a **Reopen Visit** button that prompts for a reason (a small inline prompt or `window.prompt`) and POSTs:

```jsx
const reopen = useMutation({
  mutationFn: (reason) => api.post(`/pellets/visits/${visit.id}/reopen`, { reason }).then(r => r.data),
  onSuccess: () => { qc.invalidateQueries({ queryKey: ['pellet-patient', patientId] });
                     qc.invalidateQueries({ queryKey: ['pellet-patient-counts'] }) },
})
```

When `visit.reopened_at` is set, show an amber banner: `Reopened by {visit.reopened_by} — {visit.reopened_reason}. Editing enabled.` with a **Done Editing** button that POSTs `/pellets/visits/${visit.id}/close-reopen` (same invalidation).

- [ ] **Step 3: Add per-dose lot correction (only while reopened)**

When `visit.reopened_at` is set, each dose row gets an editable lot (reuse the existing lot-picker/select pattern used by the bag-fill UI in this file or a sibling pellet component; if none is readily reusable, a simple `<select>` of lots for the dose type fetched from the existing lots endpoint is acceptable). On change, PATCH:

```jsx
const correctDose = useMutation({
  mutationFn: ({ doseId, lot_id, quantity }) =>
    api.patch(`/pellets/visits/${visit.id}/doses/${doseId}`, { lot_id, quantity }).then(r => r.data),
  onSuccess: () => { qc.invalidateQueries({ queryKey: ['pellet-patient', patientId] }) },
  onError: (e) => setDoseErr(e?.response?.data?.detail || 'Could not update dose'),
})
```

Surface the backend 409/422 detail (e.g. insufficient stock) inline near the dose.

- [ ] **Step 4: Add a "Missing lot" badge on the visit card**

When `visit.missing_lot` is true, show a small amber "Missing Lot" badge on the card header (Title Case). Dates via the project date formatter (MM/DD/YYYY).

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build` → succeeds. Paste the tail.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/PelletPatientDetail.jsx
git commit -m "feat(pellet): reopen/close + dose correction UI on visit card"
```

---

## Task 6: Frontend — missing-lot dashboard view + badge in the list

**Files:**
- Modify: the pellet patients list/dashboard page (the page that renders the view tabs + counts from `/pellets/patient-view-counts` and `/pellets/patients?view=`). Identify it by searching for `patient-view-counts` / `PATIENT_VIEWS` usage in `frontend/src`.

- [ ] **Step 1: Add the "Missing Lot" tab + count**

Add a "Missing Lot" entry to the view tab strip bound to the `missing_lot` view, showing the `missing_lot` count from the counts endpoint (styled amber to signal it needs attention). Clicking it lists patients via `?view=missing_lot`.

- [ ] **Step 2: Badge in the list row (if rows surface visit status)**

If the list rows show per-visit info, add the same small "Missing Lot" badge when a row's visit is flagged. (If the list is patient-level only, the tab + count is sufficient — don't force a per-row badge.)

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build` → succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/<pellet-list-page>.jsx
git commit -m "feat(pellet): missing-lot view tab + count on the dashboard"
```

---

## Task 7: Docs — pellet manual section

**Files:**
- Modify: `backend/app/services/manual_seed.py`

- [ ] **Step 1: Add a section to `PELLET_MANUAL_SECTIONS`**

Find `PELLET_MANUAL_SECTIONS = [` (registry key `pellets` in `MANUAL_SEEDS`). Add a new tuple with a unique slug `reopen-correct-visit`, title `Reopening & Correcting a Past Visit`, and a `sort_order` that keeps the list ascending (pick based on the real neighbors). Body:

```
A completed visit (inserted or billed) — or a cancelled one — can be reopened
by a manager to fix mistakes such as a missing or wrong lot number.

**Reopen:** On the visit, click **Reopen Visit** and enter a reason. The visit
moves to an editable state (a banner shows who reopened it and why).

**Correct doses:** While reopened, each dose's lot and quantity are editable.
Binding the correct lot pulls it from inventory (and returns the old lot if you
swap) — so fixing a missing lot also corrects your on-hand counts. Historical
(pre-system) visits are recorded only; they never move stock.

**Close:** Click **Done Editing**. The visit returns to its prior status — a
billed visit stays billed; an un-cancelled visit completes as inserted.

**Finding visits to fix:** the **Missing Lot** tab on the pellet dashboard lists
visits that were inserted or billed without a lot recorded.
```

- [ ] **Step 2: Validate it parses**

```bash
cd backend && python -c "from app.services import manual_seed as m; \
slugs=[s[0] for s in m.PELLET_MANUAL_SECTIONS]; \
assert 'reopen-correct-visit' in slugs and len(slugs)==len(set(slugs)); print('OK', slugs)"
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/manual_seed.py
git commit -m "docs(manual): pellet reopen & correct visit section"
```

---

## Final verification (after all tasks)

- [ ] `cd backend && pytest -q` → full suite green (no regression in pellet stock/live flow).
- [ ] `cd frontend && npm run build` → succeeds.
- [ ] Dispatch a final code reviewer over the whole diff (focus: stock reconciliation correctness — no double-count, no phantom stock; historical neutrality; MANAGE gating).
- [ ] Use `superpowers:finishing-a-development-branch`.

## Self-review notes

- **Spec coverage:** reopen from inserted/billed/cancelled (T2); close returns billed→billed else inserted (T2); same-as-live inventory with historical neutrality (T3); MANAGE gating (T2/T3); audit entries (T2/T3); missing-lot flag query + dashboard + badge (T1/T4/T5/T6); manual (T7).
- **Deviation from spec (noted to user):** the spec said "reuse existing in-progress edit endpoints"; planning found those don't return stock for already-`inserted` doses, so editing routes through a dedicated `PATCH .../doses/{id}` correction endpoint with one shared reconciliation rule. This realizes the spec's "same rules as live flow" decision correctly. Close-reopen also finalizes dangling doses to `inserted` — a refinement so a reopened+edited visit re-closes consistently.
- **Naming consistency:** `_visit_missing_lot`, `reopen_visit`, `close_reopen`, `correct_dose`, `_patient_has_missing_lot`, columns `reopened_at/by/reason`,`pre_reopen_status`, view `missing_lot` — used identically across tasks.
- **YAGNI:** no re-bill workflow, no dose-delete endpoint (correction + append cover the cases), `missing_lot` computed (no stored column to drift).
