# LARC Patient-Owned Close-Out Design

**Status:** Approved 2026-06-17. Give patient-owned LARC devices a way to leave the active Device
Tracking list after insertion — a close-out that reaches the existing terminal `billed` state without
a claim number.

## Problem
The active LARC list is driven by `assignment_buckets()` (`app/services/larc/workflow.py`): an
assignment drops off only when `status` becomes `billed` or `cancelled`. The normal path is
`checked_out → inserted → billed (terminal)`. The only transition to `billed` is `POST
/larc/assignments/{id}/bill` (`mark_billed`), which **explicitly rejects patient-owned devices**
(`larc.py:2669`) with: *"This is a Patient-Owned device — WWC does not bill insurance for it. Close
out the assignment without a claim number or change the device's ownership first."* But that
"close out without a claim number" path does not exist. So a patient-owned device, once inserted,
is stuck in the `inserted_not_billed` bucket forever (short of misrepresenting ownership or
cancelling a successful insertion).

## Decisions (from brainstorming)
- **Terminal state:** reuse the existing `billed` status (claim_number left null) + an audit entry —
  it drops off every list automatically; no new status, no filter/schema changes.
- **Scope:** LARC insertion path only (`status == "inserted"`). The office-procedure ("consumed")
  flow is deferred.

## Architecture

### Backend — new endpoint (`app/routers/larc.py`, beside `mark_billed`)
`POST /larc/assignments/{assignment_id}/close-out`, `Tier.WORK`. No request body needed (no claim).
- `a = _load_assignment(db, assignment_id)`
- `_block_if_closed_or_billed(a, action="close out")` (rejects already billed/closed/cancelled)
- Require `a.status == "inserted"` → else `409` ("Can only close out an inserted assignment
  (current status: {a.status})").
- Require the device is patient-owned: `a.device and (a.device.ownership or "wwc_owned") ==
  "patient_owned"` → else `409` ("Only patient-owned devices are closed out without a claim; bill
  this device via /bill.").
- Action (mirrors `mark_billed` minus the claim):
  - `a.claim_number` stays `None`
  - `a.billed_at = now_utc_naive()`, `a.billed_by = by`, `a.status = "billed"`
  - `_mark_milestone(a, "billed", status="done", by=by)`
  - `if a.device: a.device.status = "billed"`
  - `log_audit(db, actor=by, action="closed_no_claim", device=a.device, assignment=a,
    summary=f"Closed patient-owned device for {a.patient_name} (no claim)", detail={...})`
  - `db.commit(); db.refresh(a)`; return `_assignment_dict(a, include_milestones=True)`
- `mark_billed` (`/bill`) is unchanged — it still 409s patient-owned.

No schema change: `claim_number` is already nullable; `billed`/`closed_no_claim` need no new columns.

### Frontend (`frontend/src/pages/LarcAssignment.jsx` → `BilledBody`)
The "Billed" milestone card branches on ownership + claim:
- **`status === "inserted"` and `device_ownership === "patient_owned"`:** render a
  **"Close Out — Patient Paid (No Claim)"** button (not the claim-# input). On click →
  `api.post('/larc/assignments/${a.id}/close-out')` → `invalidateLarcLists(qc, a.id)`; `onError`
  alerts the detail. A one-line hint: "Patient (or their pharmacy plan) paid — WWC bills nothing."
- **`status === "billed"` and no `claim_number`:** show "✓ Closed — patient-owned (no claim) on
  {fmt.date(billed_at)} by {billed_by split @}" instead of the "Billed under claim #…" line.
- **All other ownerships / claim present:** unchanged (claim-# input + "Save & close"; the existing
  "mark inserted first" guidance for non-inserted statuses stays).

`_assignment_dict` already exposes `device_ownership`, `claim_number`, `billed_at`, `billed_by`,
`status` — no new response fields needed.

## Testing
- **Backend** (`backend/tests/test_larc_closeout.py`): seed a patient-owned device + an assignment at
  `status="inserted"` (device inserted) → `POST /close-out` → assert `status=="billed"`,
  `claim_number is None`, `billed_at` set, `device.status=="billed"`, and
  `assignment_buckets(a)` is empty (off all lists). Assert `409` when status != "inserted"; assert
  `409` when the device is `wwc_owned` (must use /bill); assert `/bill` STILL 409s a patient-owned
  inserted assignment (regression guard that the bill path is unchanged).
- **Frontend:** `npm run build` clean.
- **Authenticated walk-through** (`backend/tests/test_larc_closeout_walkthrough.py`): patient-owned
  device → record `inserted` outcome → it's in `inserted_not_billed` → `/close-out` → `billed`,
  buckets empty, claim null. (`client` super-admin fixture.)

## File structure
- Modify `backend/app/routers/larc.py` — add the `close_out` endpoint near `mark_billed`.
- Modify `frontend/src/pages/LarcAssignment.jsx` — `BilledBody` ownership branch + close-out button.
- Create `backend/tests/test_larc_closeout.py`, `backend/tests/test_larc_closeout_walkthrough.py`.

## Out of scope (YAGNI)
No new status or column; office-procedure ("consumed") patient-owned flow deferred; no change to how
billable (`wwc_owned`/`wwc_claimed`) devices are billed; no bulk close-out.

## Conventions
MM/DD/YYYY dates, Title Case button label ("Close Out — Patient Paid (No Claim)"); `now_utc_naive()`
never `datetime.utcnow()`; `Tier.WORK` (same as `/bill`); deploy `--project=wwc-solutions` + `--tag=`.
