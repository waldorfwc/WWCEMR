# LARC Patient-Owned Close-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a patient-owned LARC device that's been inserted be closed out (no claim) so it reaches the terminal `billed` state and drops off the active Device Tracking list.

**Architecture:** A new `POST /larc/assignments/{id}/close-out` endpoint mirrors `mark_billed` but leaves `claim_number` null and is gated to patient-owned + inserted assignments. The frontend `BilledBody` card shows a "Close Out — Patient Paid (No Claim)" button for that case and a "Closed (no claim)" confirmation afterward. No schema or status changes — reuse `billed`.

**Tech Stack:** FastAPI, SQLAlchemy, pytest (backend); React + react-query (frontend).

**Spec:** `docs/superpowers/specs/2026-06-17-larc-patient-owned-closeout-design.md`

**Conventions:** MM/DD/YYYY dates, Title Case button label; `now_utc_naive()` never `datetime.utcnow()`; backend pytest via `./venv/bin/python -m pytest ...` from `backend/`; frontend `npm run build` from `frontend/`.

**Grounding facts (verified in `app/routers/larc.py` unless noted):**
- `mark_billed` (`@router.post("/assignments/{assignment_id}/bill")`, line ~2657) requires `a.status == "inserted"` and **rejects** patient-owned (`a.device and (a.device.ownership or "wwc_owned") == "patient_owned"` → 409). Leave it unchanged.
- Helpers: `_load_assignment(db, assignment_id)` (404 if missing); `_block_if_closed_or_billed(a, action)` (409 if `status=="billed"` or `is_active is False`); `_mark_milestone(a, kind, *, status, by, notes=None)`; `_assignment_dict(a, include_milestones=False)`; `log_audit(db, *, actor, action, device=None, assignment=None, detail=None, summary=None)` (imported from `app.services.larc.workflow`; does NOT commit — call `db.commit()` after, like `mark_billed`).
- `_assignment_dict` already returns `device_ownership`, `claim_number`, `billed_at`, `billed_by`, `status`.
- `assignment_buckets(a)` (`app/services/larc/workflow.py`) returns an empty set when `a.status in ("billed","cancelled")`.
- Ownership constants: `LARC_OWNERSHIP_VALUES`, `LARC_BILLABLE_OWNERSHIPS` in `app/models/larc.py`; `patient_owned` is NOT billable.
- Test seeding: `LarcDeviceType` needs only `name`; `LarcDevice` needs `our_id` + `device_type_id` (+ `ownership`/`status` have defaults); `LarcAssignment(chart_number, patient_name, source_flow, status, device_id, is_active)`. Tests use `create_all` (no init_db seed), `client` is super-admin.

---

## File Structure
- Modify `backend/app/routers/larc.py` — add `close_out` endpoint immediately after `mark_billed`.
- Modify `frontend/src/pages/LarcAssignment.jsx` — `BilledBody` ownership branch + close-out button.
- Create `backend/tests/test_larc_closeout.py`, `backend/tests/test_larc_closeout_walkthrough.py`.

---

### Task 1: Backend `/close-out` endpoint

**Files:**
- Modify: `backend/app/routers/larc.py` (after `mark_billed`, ~line 2689)
- Test: `backend/tests/test_larc_closeout.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_larc_closeout.py
"""Patient-owned LARC close-out: an inserted patient-owned device is closed
without a claim, reaching the terminal 'billed' state so it drops off the list.
`client` is the super-admin fixture."""
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.services.larc.workflow import assignment_buckets


def _seed(db, *, ownership="patient_owned", a_status="inserted", d_status="inserted"):
    dt = LarcDeviceType(name=f"Liletta-{ownership}-{a_status}")
    db.add(dt); db.flush()
    d = LarcDevice(our_id=f"LAR-{ownership}-{a_status}", device_type_id=dt.id,
                   ownership=ownership, status=d_status)
    db.add(d); db.flush()
    a = LarcAssignment(chart_number="MRN-PO", patient_name="Doe, Pat",
                       source_flow="larc", status=a_status, device_id=d.id, is_active=True)
    db.add(a); db.commit(); db.refresh(a)
    return a, d


def test_close_out_patient_owned_inserted(client, db):
    a, d = _seed(db)
    r = client.post(f"/api/larc/assignments/{a.id}/close-out")
    assert r.status_code == 200, r.text
    db.refresh(a); db.refresh(d)
    assert a.status == "billed"
    assert a.claim_number is None
    assert a.billed_at is not None and a.billed_by
    assert d.status == "billed"
    assert assignment_buckets(a) == set()      # off every list


def test_close_out_requires_inserted(client, db):
    a, _ = _seed(db, a_status="checked_out", d_status="checked_out")
    r = client.post(f"/api/larc/assignments/{a.id}/close-out")
    assert r.status_code == 409
    assert "inserted" in r.json()["detail"].lower()


def test_close_out_rejects_non_patient_owned(client, db):
    a, _ = _seed(db, ownership="wwc_owned")
    r = client.post(f"/api/larc/assignments/{a.id}/close-out")
    assert r.status_code == 409
    assert "patient-owned" in r.json()["detail"].lower()


def test_bill_still_rejects_patient_owned(client, db):
    """Regression: the /bill path is unchanged — patient-owned still 409s there."""
    a, _ = _seed(db)
    r = client.post(f"/api/larc/assignments/{a.id}/bill", json={"claim_number": "CLM-1"})
    assert r.status_code == 409
    assert "patient-owned" in r.json()["detail"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_closeout.py -v`
Expected: FAIL — `404`/no route for `/close-out` (and the bill regression test passes already).

- [ ] **Step 3: Add the endpoint**

In `backend/app/routers/larc.py`, immediately after the `mark_billed` function (after its `return _assignment_dict(...)`, ~line 2689), add:

```python
@router.post("/assignments/{assignment_id}/close-out")
def close_out(assignment_id: str,
              db: Session = Depends(get_db),
              current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Close out an inserted PATIENT-OWNED assignment without a claim number.
    WWC does not bill insurance for patient-owned devices, so they can't go
    through /bill; this reaches the same terminal 'billed' state (claim null)
    so the assignment + device drop off the active Device Tracking list."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="close out")
    if a.status != "inserted":
        raise HTTPException(
            status_code=409,
            detail=f"Can only close out an inserted assignment (current status: {a.status})")
    if not (a.device and (a.device.ownership or "wwc_owned") == "patient_owned"):
        raise HTTPException(
            status_code=409,
            detail="Only patient-owned devices are closed out without a claim; "
                   "bill this device via /bill.")
    by = current_user.get("email") or "system"
    a.claim_number = None
    a.billed_at = now_utc_naive()
    a.billed_by = by
    a.status = "billed"
    _mark_milestone(a, "billed", status="done", by=by)
    if a.device:
        a.device.status = "billed"
    log_audit(db, actor=by, action="closed_no_claim",
              device=a.device, assignment=a,
              summary=f"Closed patient-owned device for {a.patient_name} (no claim)",
              detail={"ownership": (a.device.ownership if a.device else None)})
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)
```

(Confirm `now_utc_naive`, `log_audit`, `_mark_milestone`, `_load_assignment`, `_block_if_closed_or_billed`, `_assignment_dict`, `requires_tier`, `Module`, `Tier`, `get_db`, `Session`, `HTTPException` are already imported in this module — they are, since `mark_billed` uses all of them.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_closeout.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/routers/larc.py tests/test_larc_closeout.py
git commit -m "feat(larc): patient-owned close-out endpoint (terminal billed, no claim)"
```

---

### Task 2: Frontend — close-out button in `BilledBody`

**Files:**
- Modify: `frontend/src/pages/LarcAssignment.jsx` (the `BilledBody` function, ~line 1175)

Current `BilledBody` (verified): a `save` mutation POSTs `/larc/assignments/${a.id}/bill` with `{claim_number}`; if `a.status === 'billed'` it shows "✓ Billed under claim #…"; if `a.status !== 'inserted'` it shows a "mark inserted first" hint; otherwise it renders the claim-# input + "Save & close". `invalidateLarcLists(qc, a.id)` is the existing cache-refresh helper. `a.device_ownership`, `a.claim_number`, `a.billed_at`, `a.billed_by` are available on `a`.

- [ ] **Step 1: Read the current `BilledBody`**

Run: `cd frontend && grep -n "function BilledBody\|invalidateLarcLists\|device_ownership\|status === 'billed'\|status !== 'inserted'\|Save & close" src/pages/LarcAssignment.jsx`
Read the `BilledBody` function fully so the edits land precisely.

- [ ] **Step 2: Add the close-out mutation + branches**

Inside `BilledBody`, add a close-out mutation next to the existing `save` mutation:

```javascript
  const closeOut = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/close-out`).then(r => r.data),
    onSuccess: () => invalidateLarcLists(qc, a.id),
    onError: (e) => alert(e?.response?.data?.detail || 'Close-out failed'),
  })
  const isPatientOwned = (a.device_ownership || 'wwc_owned') === 'patient_owned'
```

In the `a.status === 'billed'` branch, distinguish a no-claim close-out. Replace:

```javascript
  if (a.status === 'billed') {
    return (
      <div className="text-[11px] text-green-700">
        ✓ Billed under claim #{a.claim_number} on {a.billed_at && fmt.date(a.billed_at)}
        {a.billed_by && ` by ${a.billed_by.split('@')[0]}`}
      </div>
    )
  }
```

with:

```javascript
  if (a.status === 'billed') {
    return (
      <div className="text-[11px] text-green-700">
        {a.claim_number
          ? <>✓ Billed under claim #{a.claim_number}</>
          : <>✓ Closed — patient-owned (no claim)</>}
        {' '}on {a.billed_at && fmt.date(a.billed_at)}
        {a.billed_by && ` by ${a.billed_by.split('@')[0]}`}
      </div>
    )
  }
```

In the `a.status === 'inserted'` path (the final `return` that renders the claim input), branch for patient-owned BEFORE the claim input. Add, right before that final `return (`:

```javascript
  if (isPatientOwned) {
    return (
      <div className="space-y-2 text-[12px]">
        <div className="text-[11px] text-gray-600">
          Patient (or their pharmacy plan) paid for this device — WWC bills nothing.
          Close it out to remove it from the active list.
        </div>
        <button className="btn-primary text-[11px]"
                onClick={() => closeOut.mutate()}
                disabled={closeOut.isPending}>
          {closeOut.isPending ? 'Closing…' : 'Close Out — Patient Paid (No Claim)'}
        </button>
      </div>
    )
  }
```

(The existing `a.status !== 'inserted'` guard already returns earlier, so reaching this point means `status === 'inserted'`. Non-patient-owned still falls through to the unchanged claim-# input + "Save & close".)

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: succeeds, no errors referencing `LarcAssignment.jsx`.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/pages/LarcAssignment.jsx
git commit -m "feat(larc): close-out button for inserted patient-owned devices"
```

---

### Task 3: Authenticated walk-through

**Files:**
- Create: `backend/tests/test_larc_closeout_walkthrough.py`

- [ ] **Step 1: Write the walk-through test**

```python
# backend/tests/test_larc_closeout_walkthrough.py
"""Authenticated walk-through: a patient-owned LARC device is inserted, then
closed out (no claim) and drops off the active list. `client` is super-admin."""
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.services.larc.workflow import assignment_buckets


def test_closeout_walkthrough(client, db, capsys):
    log = []
    dt = LarcDeviceType(name="Liletta-WT")
    db.add(dt); db.flush()
    d = LarcDevice(our_id="LAR-WT-1", device_type_id=dt.id,
                   ownership="patient_owned", status="inserted")
    db.add(d); db.flush()
    a = LarcAssignment(chart_number="MRN-WT", patient_name="Roe, Pat",
                       source_flow="larc", status="inserted", device_id=d.id, is_active=True)
    db.add(a); db.commit(); db.refresh(a)
    log.append("seeded a patient-owned device, inserted (sits in 'inserted_not_billed')")

    # 1. /bill rejects it (the gap the user reported).
    r = client.post(f"/api/larc/assignments/{a.id}/bill", json={"claim_number": "CLM-X"})
    assert r.status_code == 409 and "patient-owned" in r.json()["detail"].lower()
    log.append(f"1. POST /bill → 409: \"{r.json()['detail'][:60]}…\"")

    # 2. /close-out reaches the terminal billed state with no claim.
    r = client.post(f"/api/larc/assignments/{a.id}/close-out")
    assert r.status_code == 200, r.text
    db.refresh(a)
    assert a.status == "billed" and a.claim_number is None and a.billed_by
    log.append(f"2. POST /close-out → 200; status 'billed', no claim, by {a.billed_by}")

    # 3. It's off every active list.
    assert assignment_buckets(a) == set()
    log.append("3. assignment_buckets empty → dropped off the active Device Tracking list")

    with capsys.disabled():
        print("\n  -- LARC patient-owned close-out walk-through (authenticated) --")
        for line in log:
            print("   " + line)
```

- [ ] **Step 2: Run it**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_larc_closeout_walkthrough.py -v -s`
Expected: PASS, log prints.

- [ ] **Step 3: Commit**

```bash
cd backend && git add tests/test_larc_closeout_walkthrough.py
git commit -m "test(larc): patient-owned close-out walk-through"
```

---

## Final Verification (after all tasks)
- [ ] `cd backend && ./venv/bin/python -m pytest tests/ -k "larc_closeout or larc_billing_gate" -v` → all PASS (the billing-gate test confirms `/bill` behavior is unbroken).
- [ ] `cd frontend && npm run build` → clean.
- [ ] No new failures vs the documented baseline (`cd backend && ./venv/bin/python -m pytest tests/ -k "larc" -q`).

## Notes for the implementer
- The endpoint reaches the SAME terminal `billed` status as `/bill`; that's deliberate so all existing list/bucket filters drop it with zero changes. The only distinguishing data is `claim_number is None` (used by the UI to label it "Closed — patient-owned (no claim)").
- Do NOT modify `mark_billed` — its patient-owned 409 stays; `test_bill_still_rejects_patient_owned` guards that.
- If a seeded `LarcDevice`/`LarcAssignment` row hits an unexpected NOT NULL column, add the minimal field to the test helper (keep assertions identical) and note it.
