# Pellet Un-cancel (Cancelled-Visit Reopen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-enable reopening a `cancelled` pellet visit by reversing the cancel's stock credit — re-pull exactly the `returned` doses, then ride the existing reopen→correct→close machinery.

**Architecture:** One new branch in `reopen_visit` re-pulls the doses cancel set to `returned` (restoring them to `pulled`), atomically (shortfall → 409, full rollback). Everything downstream — corrections, the MANAGE append gate, and `close_reopen` (`pulled`→`inserted`, `cancelled`→`inserted`) — is reused unchanged. Plus re-enabling `cancelled` in the reopenable set, the frontend button gate, and the manual.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React (frontend), pytest.

**Spec:** `docs/superpowers/specs/2026-06-23-pellet-uncancel-reopen-design.md`

---

## Verified facts

- `cancel_visit` (`pellet.py:4659`) credits stock back **only for doses in `("pulled","added")`**, setting them to `"returned"` (lot_id + quantity preserved). `planned`/`inserted`/`disposed`/`reduced` untouched. So `returned` is exactly the re-pull set.
- `reopen_visit` (`pellet.py:5909`) currently changes no doses; `_REOPENABLE_STATUSES = {"inserted","billed"}`.
- `close_reopen` (`pellet.py:5938`) already finalizes `planned/pulled/added`→`inserted` and maps `pre_reopen_status=="cancelled"`→`inserted` — no change needed.
- Helpers (all in `pellet.py`): `_require_visit_location(v)` (raises if no location — used by cancel), `_get_or_create_stock(db, lot_id, location)`, `_adjust_stock(db, stock, delta)` (delta<0 → conditional decrement, raises `HTTPException(409)` if balance `< |delta|`), `_audit(db, *, actor, action, lot_id=, location=, delta_doses=, summary=, detail=)`, `_load_visit(db, id)` (joinedloads doses), `now_utc_naive`. `HTTPException` is imported.
- Test file `backend/tests/test_pellet_reopen_visit.py` has helpers `_mgr/_patient/_dose_type/_lot/_visit/_client/_stock` (where `_lot(db, dt, qty=, number=)` creates a `PelletLot` + active `PelletStock`, and `_stock(db, lot, loc="white_plains")` fetches the stock row). `_visit(db, p, status=, historical=, location=)` builds a visit.
- The obsolete test to replace: `test_reopen_cancelled_now_rejected` (`test_pellet_reopen_visit.py:140`) — asserts cancelled reopen → 409; will break once cancelled is reopenable.
- Frontend gate: `frontend/src/pages/PelletPatientDetail.jsx:1041` →
  `const canReopen = canManage && ['inserted', 'billed'].includes(visit.status) && !isReopened`.

---

## Task 1: Backend — un-cancel re-pull branch in `reopen_visit`

**Files:**
- Modify: `backend/app/routers/pellet.py`
- Test: `backend/tests/test_pellet_reopen_visit.py`

- [ ] **Step 1: Replace the obsolete test + add un-cancel tests**

In `backend/tests/test_pellet_reopen_visit.py`, DELETE `test_reopen_cancelled_now_rejected` and add (reuse existing helpers; `PelletVisitDose` is already imported):

```python
def test_reopen_cancelled_repulls_returned_dose(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p, status="cancelled")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="returned", lot_id=lot.id)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "wrongly cancelled"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_progress"
    assert body["pre_reopen_status"] == "cancelled"
    assert _stock(db, lot).doses_on_hand == 7   # 10 - 3 re-pulled
    db.refresh(d); assert d.status == "pulled"


def test_reopen_cancelled_insufficient_stock_409_atomic(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=2)  # only 2 on hand
    v = _visit(db, p, status="cancelled")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="returned", lot_id=lot.id)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "x"})
    assert r.status_code == 409
    db.refresh(v); db.refresh(d)
    assert v.status == "cancelled" and v.reopened_at is None   # unchanged
    assert _stock(db, lot).doses_on_hand == 2                  # unchanged
    assert d.status == "returned"                              # unchanged


def test_reopen_cancelled_no_returned_doses_moves_no_stock(client_factory, db):
    # cancelled-from-inserted: doses already inserted, none returned
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p, status="cancelled")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="inserted", lot_id=lot.id)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "x"})
    assert r.status_code == 200
    assert _stock(db, lot).doses_on_hand == 10   # untouched
    db.refresh(d); assert d.status == "inserted"


def test_reopen_cancelled_historical_is_stock_neutral(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p, status="cancelled", historical=True)
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="returned", lot_id=lot.id)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    r = client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "x"})
    assert r.status_code == 200
    assert _stock(db, lot).doses_on_hand == 10   # unchanged — historical
    db.refresh(d); assert d.status == "pulled"


def test_reopen_cancelled_then_close_returns_inserted_no_extra_stock(client_factory, db):
    p = _patient(db); dt = _dose_type(db); lot = _lot(db, dt, qty=10)
    v = _visit(db, p, status="cancelled")
    d = PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=3,
                        position=1, status="returned", lot_id=lot.id)
    db.add(d); db.commit(); db.refresh(d)
    client = _client(client_factory, db)
    client.post(f"/api/pellets/visits/{v.id}/reopen", json={"reason": "x"})
    assert _stock(db, lot).doses_on_hand == 7
    r = client.post(f"/api/pellets/visits/{v.id}/close-reopen")
    assert r.json()["status"] == "inserted"
    assert _stock(db, lot).doses_on_hand == 7   # no further movement
    db.refresh(d); assert d.status == "inserted"
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `cd backend && pytest tests/test_pellet_reopen_visit.py -k "cancelled" -q`
Expected: the new tests FAIL — reopening a cancelled visit currently 409s (`cancelled` not in `_REOPENABLE_STATUSES`).

- [ ] **Step 3: Re-enable `cancelled` + add the re-pull branch**

In `backend/app/routers/pellet.py`:

(a) Change the constant:
```python
_REOPENABLE_STATUSES = {"inserted", "billed", "cancelled"}
```

(b) In `reopen_visit`, immediately after `by = current_user.get("email") or "system"` and BEFORE `v.pre_reopen_status = v.status`, insert the un-cancel branch:

```python
    # Un-cancel: reopening a cancelled visit reverses what cancel did to stock.
    # cancel_visit credited stock back only for the doses it set to "returned";
    # re-pull exactly those (restoring them to "pulled") so the visit becomes a
    # normal reopened visit with no "returned" doses left. Atomic: a shortfall
    # 409s and the whole re-pull is rolled back — never a partial un-cancel.
    if v.status == "cancelled":
        returned_doses = [d for d in (v.doses or []) if d.status == "returned"]
        location = (_require_visit_location(v)
                    if (returned_doses and not v.is_historical) else v.location)
        try:
            for d in returned_doses:
                if (not v.is_historical) and d.lot_id:
                    stock = _get_or_create_stock(db, d.lot_id, location)
                    _adjust_stock(db, stock, -(d.quantity))
                    _audit(db, actor=by, action="visit_uncancel_repull",
                           lot_id=d.lot_id, location=location,
                           delta_doses=-(d.quantity),
                           summary=(f"Re-pulled {d.quantity} "
                                    f"{d.dose_type.label if d.dose_type else ''} "
                                    f"from stock (visit un-cancelled)"),
                           detail={"visit_id": str(v.id), "visit_dose_id": str(d.id)})
                d.status = "pulled"
                d.pulled_at = now_utc_naive()
                d.pulled_by = by
                d.resolved_at = None
                d.resolved_by = None
        except HTTPException:
            db.rollback()
            raise
```

The existing tail (`v.pre_reopen_status = v.status` → flips to `in_progress`, stamps reopen fields, audits `visit_reopened`, commits) runs unchanged. At that point `v.status` is still `"cancelled"`, so `pre_reopen_status` is correctly recorded as `"cancelled"`.

- [ ] **Step 4: Run tests**

Run: `cd backend && pytest tests/test_pellet_reopen_visit.py -q` → all pass (the 5 new + existing).
Then regression: `cd backend && pytest tests/ -k pellet -q` → green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/pellet.py backend/tests/test_pellet_reopen_visit.py
git commit -m "feat(pellet): un-cancel — reopen a cancelled visit re-pulls returned doses"
```

---

## Task 2: Frontend — allow reopen on cancelled visits

**Files:**
- Modify: `frontend/src/pages/PelletPatientDetail.jsx`

- [ ] **Step 1: Add `cancelled` to the reopen gate**

At `frontend/src/pages/PelletPatientDetail.jsx:1041`, change:
```jsx
const canReopen = canManage && ['inserted', 'billed'].includes(visit.status) && !isReopened
```
to:
```jsx
const canReopen = canManage && ['inserted', 'billed', 'cancelled'].includes(visit.status) && !isReopened
```

- [ ] **Step 2: Build**

Run: `cd frontend && npm run build` → succeeds. Paste the tail.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/PelletPatientDetail.jsx
git commit -m "feat(pellet): show Reopen Visit on cancelled visits"
```

---

## Task 3: Docs — restore cancelled wording in the manual

**Files:**
- Modify: `backend/app/services/manual_seed.py`

- [ ] **Step 1: Update the `reopen-correct-visit` pellet section**

In `backend/app/services/manual_seed.py`, find the `reopen-correct-visit` section in `PELLET_MANUAL_SECTIONS`. Its opening currently reads (after the cancelled-deferral fix) something like "A completed visit (inserted or billed) can be reopened ...". Update the body so it:
- Restores cancelled to the opening, e.g.: "A completed visit (inserted or billed) — or a cancelled one — can be reopened by a manager to fix mistakes such as a missing or wrong lot number."
- Adds an un-cancel note to the **Close** paragraph or as its own line, e.g.: "Reopening a cancelled visit un-cancels it — the pellets it returned to stock are pulled back out, and it completes as inserted on close. If there isn't enough on hand to pull them back, the reopen is blocked until you receive stock."

Keep the rest of the section intact and match the file's triple-quoted formatting.

- [ ] **Step 2: Validate it parses**

```bash
cd backend && (source .venv/bin/activate 2>/dev/null || source venv/bin/activate); \
python -c "from app.services import manual_seed as m; \
print('ok', [s[0] for s in m.PELLET_MANUAL_SECTIONS])"
```
Expected: prints `ok [...]` including `reopen-correct-visit`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/manual_seed.py
git commit -m "docs(manual): restore cancelled-visit reopen (un-cancel) wording"
```

---

## Final verification (after all tasks)

- [ ] `cd backend && pytest -q` → full suite green.
- [ ] `cd frontend && npm run build` → succeeds.
- [ ] Dispatch a final reviewer focused on: the re-pull set is exactly `returned` doses; atomic rollback on shortfall (no partial un-cancel, no negative stock); the cancel↔un-cancel round trip is net-zero; close still returns `inserted` with no extra movement.
- [ ] Use `superpowers:finishing-a-development-branch`.

## Self-review notes

- **Spec coverage:** re-pull `returned` doses → `pulled` (T1); atomic 409 on shortfall (T1, explicit `db.rollback()`); historical stock-neutral (T1); re-enable constant + frontend gate (T1/T2); manual (T3). `close_reopen` reused unchanged (verified it already finalizes `pulled`→`inserted`, `cancelled`→`inserted`).
- **YAGNI:** only `returned` doses re-pulled (cancel touched nothing else); no `close_reopen`/`cancel_visit` changes; restore to `pulled` (not `inserted`) so the existing close finalize applies.
- **Naming:** audit action `visit_uncancel_repull`; constant `_REOPENABLE_STATUSES`; consistent with the shipped reopen feature.
