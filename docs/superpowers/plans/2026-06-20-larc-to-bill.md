# LARC "To Bill" Worklist — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "To Bill" LARC nav tab listing practice-owned checked-out-not-billed devices with an inline claim-# field that bills inserted ones via the existing endpoint.

**Architecture:** One new read endpoint (`GET /api/larc/to-bill`) feeds one new page (`LarcToBill.jsx`); billing reuses the existing `POST /api/larc/assignments/{id}/bill`. No changes to the billing logic or milestones.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (`client`/`db` fixtures, super-admin client, `/api/...`); React + Vite + react-query (no JS test runner — verify via `npm run build` + manual).

**Spec:** `docs/superpowers/specs/2026-06-20-larc-to-bill-design.md`

**Conventions:** `now_utc_naive()`; backend tests seed via models on `db`; run from `backend/` with `source venv/bin/activate`; scoped `git add`; Title Case UI labels; MM/DD/YYYY via `fmt.date`.

---

## File Structure
- **Backend:** `app/routers/larc.py` — add `GET /to-bill` (new endpoint). Test: `tests/test_larc_to_bill.py` (new).
- **Frontend:** `src/pages/LarcToBill.jsx` (new page), `src/components/larc/LarcNav.jsx` (nav tab), `src/routes.jsx` (route).

---

## Task 1: Backend `GET /api/larc/to-bill`

**Files:**
- Modify: `backend/app/routers/larc.py` (add the route after `list_assignments`, ~line 1246)
- Test: `backend/tests/test_larc_to_bill.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_larc_to_bill.py`:

```python
"""GET /api/larc/to-bill — practice-owned, checked-out, not-yet-billed worklist."""
from datetime import datetime
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType, LarcMilestone
from app.services.larc.workflow import spawn_milestones


def _dt(db, name="Mirena"):
    dt = LarcDeviceType(name=name, category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _assignment(db, dt, ownership, our_id, *, checked_out=True, inserted=False,
                billed=False, co_at=None):
    dev = LarcDevice(our_id=our_id, device_type_id=dt.id, status="checked_out", ownership=ownership)
    db.add(dev); db.commit(); db.refresh(dev)
    a = LarcAssignment(chart_number=f"C{our_id}", patient_name=f"Pt {our_id}",
                       device_type_id=dt.id, device_id=dev.id, source_flow="in_stock",
                       status="inserted" if inserted else "in_progress")
    db.add(a); db.commit(); db.refresh(a)
    spawn_milestones(db, a); db.commit()
    by_kind = {m.kind: m for m in a.milestones}

    def mark(kind, when=None):
        m = by_kind.get(kind)
        if m:
            m.status = "done"
            m.completed_at = when or datetime(2026, 6, 1, 9, 0, 0)
    if checked_out:
        mark("device_checked_out", co_at)
    if inserted:
        mark("device_inserted")
    if billed:
        mark("billed")
    db.commit()
    return a


def test_to_bill_lists_practice_owned_checked_out_unbilled(client, db):
    dt = _dt(db)
    inserted = _assignment(db, dt, "wwc_owned", "WWC-1", inserted=True,
                           co_at=datetime(2026, 6, 2, 9, 0, 0))
    awaiting = _assignment(db, dt, "wwc_claimed", "WWC-2", inserted=False,
                           co_at=datetime(2026, 6, 1, 9, 0, 0))
    _assignment(db, dt, "patient_owned", "PT-1", inserted=True)          # excluded: patient-owned
    _assignment(db, dt, "wwc_owned", "WWC-3", checked_out=False)          # excluded: not checked out
    _assignment(db, dt, "wwc_owned", "WWC-4", inserted=True, billed=True) # excluded: billed

    r = client.get("/api/larc/to-bill")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [it["assignment_id"] for it in body["items"]]
    assert str(inserted.id) in ids and str(awaiting.id) in ids
    assert body["total"] == 2
    # oldest checked-out first
    assert ids == [str(awaiting.id), str(inserted.id)]
    by_id = {it["assignment_id"]: it for it in body["items"]}
    assert by_id[str(inserted.id)]["inserted"] is True
    assert by_id[str(awaiting.id)]["inserted"] is False
    assert by_id[str(inserted.id)]["device_our_id"] == "WWC-1"
    assert by_id[str(awaiting.id)]["device_type_name"] == "Mirena"
```

- [ ] **Step 2: Run, expect FAIL** — `cd backend && source venv/bin/activate && pytest tests/test_larc_to_bill.py -q` → 404/405 (route missing).

- [ ] **Step 3: Implement** — in `backend/app/routers/larc.py`, add immediately after `list_assignments` (the function ending ~line 1246). Confirm `joinedload`, `LarcDevice`, `LarcDeviceType`, `requires_tier`, `Module`, `Tier`, `get_db` are imported (they are, used elsewhere):

```python
@router.get("/to-bill")
def list_to_bill(db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    """Practice-owned devices that have been checked out but not yet billed.
    Each row carries an `inserted` flag — only inserted ones are billable now
    (the /bill endpoint requires status 'inserted'). Patient-owned devices are
    excluded (WWC does not bill insurance for them)."""
    rows = (db.query(LarcAssignment)
              .options(joinedload(LarcAssignment.milestones),
                       joinedload(LarcAssignment.device).joinedload(LarcDevice.device_type))
              .filter(LarcAssignment.not_deleted(),
                      LarcAssignment.is_active.is_(True))
              .all())
    items = []
    for a in rows:
        dev = a.device
        if not dev or (dev.ownership or "wwc_owned") == "patient_owned":
            continue
        by_kind = {m.kind: m for m in (a.milestones or [])}

        def _done(kind):
            m = by_kind.get(kind)
            return m is not None and m.status in ("done", "skipped", "not_applicable")

        if not _done("device_checked_out") or _done("billed"):
            continue
        co = by_kind.get("device_checked_out")
        items.append({
            "assignment_id": str(a.id),
            "patient_name": a.patient_name,
            "chart_number": a.chart_number,
            "device_our_id": dev.our_id,
            "device_type_name": dev.device_type.name if dev.device_type else None,
            "device_ownership": dev.ownership or "wwc_owned",
            "checked_out_at": co.completed_at.isoformat() if (co and co.completed_at) else None,
            "inserted": _done("device_inserted"),
            "claim_number": a.claim_number,
        })
    items.sort(key=lambda x: x["checked_out_at"] or "")
    return {"total": len(items), "items": items}
```

Route placement note: `/to-bill` is a distinct path (not under `/assignments/{id}`), so no route-shadowing concern; placing it right after `list_assignments` keeps related code together.

- [ ] **Step 4: Run, expect PASS** — `pytest tests/test_larc_to_bill.py -q` (1 passed). Then `pytest tests/ -q -k larc` (no regressions).

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/larc.py backend/tests/test_larc_to_bill.py
git commit -m "feat(larc): GET /to-bill worklist (practice-owned, checked-out, unbilled)"
```

---

## Task 2: Frontend "To Bill" page + nav tab + route

**Files:**
- Create: `frontend/src/pages/LarcToBill.jsx`
- Modify: `frontend/src/components/larc/LarcNav.jsx` (tab after Checkouts, ~line 19), `frontend/src/routes.jsx` (import + route after Checkouts, ~line 232)

No JS test runner — verify with `cd frontend && npm run build` (`✓ built`) + manual checklist.

- [ ] **Step 1: Create `frontend/src/pages/LarcToBill.jsx`:**

```jsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Receipt } from 'lucide-react'
import api, { fmt } from '../utils/api'
import EmptyState from '../components/EmptyState'

export default function LarcToBill() {
  const { data } = useQuery({
    queryKey: ['larc-to-bill'],
    queryFn: () => api.get('/larc/to-bill').then(r => r.data),
  })
  const items = data?.items || []

  return (
    <div>
      <h1 className="page-title flex items-center gap-2">
        <Receipt size={22} className="text-plum-700" /> To Bill
      </h1>
      <p className="text-sm text-gray-500 mt-0.5 mb-4">
        Practice-owned devices checked out and awaiting a ModMed claim number.
      </p>
      {items.length === 0 ? (
        <EmptyState title="Nothing To Bill"
          message="No practice-owned devices are waiting to be billed." />
      ) : (
        <table className="table w-full text-sm">
          <thead>
            <tr>
              <th className="table-th text-left">Patient</th>
              <th className="table-th text-left">Device</th>
              <th className="table-th text-left">Checked Out</th>
              <th className="table-th text-left">Status</th>
              <th className="table-th text-left">Claim #</th>
            </tr>
          </thead>
          <tbody>
            {items.map(it => <ToBillRow key={it.assignment_id} it={it} />)}
          </tbody>
        </table>
      )}
    </div>
  )
}

function ToBillRow({ it }) {
  const qc = useQueryClient()
  const [claim, setClaim] = useState('')
  const bill = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${it.assignment_id}/bill`,
      { claim_number: claim.trim() }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-to-bill'] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Billing failed'),
  })
  return (
    <tr>
      <td className="table-td">{it.patient_name}<br />
        <span className="text-[11px] text-muted">Chart {it.chart_number}</span></td>
      <td className="table-td">{it.device_our_id}<br />
        <span className="text-[11px] text-muted">{it.device_type_name}</span></td>
      <td className="table-td">{it.checked_out_at ? fmt.date(it.checked_out_at) : '—'}</td>
      <td className="table-td">
        {it.inserted
          ? <span className="text-green-700">Inserted</span>
          : <span className="inline-block rounded bg-amber-100 text-amber-800 px-2 py-0.5 text-[11px]">
              Awaiting insertion</span>}
      </td>
      <td className="table-td">
        {it.inserted ? (
          <div className="flex gap-2 items-center">
            <input className="input w-32" placeholder="Claim #" value={claim}
                   onChange={e => setClaim(e.target.value)} />
            <button className="btn-primary text-xs" disabled={!claim.trim() || bill.isPending}
                    onClick={() => bill.mutate()}>Save</button>
          </div>
        ) : <span className="text-muted text-[11px]">—</span>}
      </td>
    </tr>
  )
}
```

Note: verify `EmptyState` import path + props against an existing page (e.g. `Larc.jsx` imports `EmptyState from '../components/EmptyState'`); match its real prop names (`title`/`message`). Verify `table`/`table-th`/`table-td`/`input`/`btn-primary` classes are the ones used elsewhere in LARC pages — mirror `LarcCheckouts.jsx`/`Larc.jsx` if any differ.

- [ ] **Step 2: Add the nav tab** — in `frontend/src/components/larc/LarcNav.jsx`, after the Checkouts entry (`{ to: '/larc/checkouts', label: 'Checkouts', tier: TIER.VIEW },`):

```jsx
    { to: '/larc/to-bill',         label: 'To Bill',         tier: TIER.VIEW },
```

- [ ] **Step 3: Add the route** — in `frontend/src/routes.jsx`: add the import near the other larc imports (after `import LarcCheckouts ...`):

```jsx
import LarcToBill from './pages/LarcToBill'
```
and the child route after the `checkouts` entry (~line 232):
```jsx
    { path: 'to-bill',         element: <LarcToBill />,         module: M.LARC, tier: TIER.VIEW },
```

- [ ] **Step 4: Build** — `cd frontend && npm run build` → `✓ built`, no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/LarcToBill.jsx frontend/src/components/larc/LarcNav.jsx frontend/src/routes.jsx
git commit -m "feat(larc): To Bill nav tab + worklist page"
```

- [ ] **Step 6: Manual checklist (from code):** the "To Bill" tab appears after Checkouts; the page lists practice-owned checked-out-unbilled devices oldest-first; inserted rows have a claim-# field + Save that bills via `/bill` and drops the row on success; not-inserted rows show "Awaiting insertion" with no claim field; empty state renders when none.

---

## Task 3: Verify + deploy

- [ ] **Step 1:** `cd backend && source venv/bin/activate && pytest tests/ -q -k larc` → all pass.
- [ ] **Step 2:** `cd frontend && npm run build` → `✓ built`.
- [ ] **Step 3 (deploy, only when the user asks):** backend first (new endpoint), then frontend:
```bash
SHA=$(git rev-parse --short HEAD)
gcloud builds submit backend/  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:$SHA  --project=wwc-solutions --region=us-east4
gcloud builds submit frontend/ --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:$SHA --project=wwc-solutions --region=us-east4
gcloud run services update backend  --region=us-east4 --project=wwc-solutions --image=...backend:$SHA
gcloud run services update frontend --region=us-east4 --project=wwc-solutions --image=...frontend:$SHA
```
No migrations (read-only endpoint, no schema change).

## Notes / risks
- The `/to-bill` filter loads all active assignments + milestones in Python (same pattern as the dashboard). Fine at current volume; if LARC assignment counts grow large, push the milestone filter into SQL later.
- `checked_out_at` comes from the `device_checked_out` milestone's `completed_at`; if that's ever null for a legitimately checked-out row, it sorts first (treated as oldest) — acceptable.
