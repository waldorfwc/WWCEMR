# Device Tracking (LARC) Reports Design

**Status:** Approved 2026-06-18. A "Reports" tab in the Device Tracking (LARC) nav mirroring the
Surgery/Pellet Reports: a filter bar (date range + location + device type) over 7 tiles, each
clickable to a drill-down list with CSV export.

## Goal
Give the practice the same operational dashboard for LARC device tracking that Surgery and Pellet
Reports provide — workflow pipeline, outstanding pharmacy enrollment, insertion throughput, billing
backlog, owed patients, inventory health, and insertion outcomes — computed from existing data.
`LarcAssignment.inserted_at` anchors completion (no migration).

## Decisions (from brainstorming)
- **7 tiles** (below): the recommended 6 + a dedicated **Outstanding Enrollment** tile (the
  pharmacy-order enrollment pipeline).
- **Filters:** date range + location + device type. The range applies to *period* tiles only;
  *snapshot* tiles are "as of now." Location + device type apply to all assignment/device tiles.
- **Completion anchor:** `LarcAssignment.inserted_at`.

## Architecture

### Backend — aggregation service (`backend/app/services/larc/reports.py`)
One pure function per tile, `(db, *, date_from, date_to, location, device_type_id)` → dict. Shared
filters:
- **device type** → `LarcAssignment.device_type_id == device_type_id` (it's on the assignment
  directly — set even for pharmacy-order assignments before a device is received).
- **location** → the assignment's device location (`LarcAssignment.device` →
  `LarcDevice.location == location`). Assignments with no device yet (pharmacy-order pre-receipt)
  don't match a *specific* location filter — they appear only when location is unfiltered.
- exclude soft-deleted assignments (`LarcAssignment.deleted_at is None` — the model has
  `SoftDeleteMixin`).
Reuse the canonical **`assignment_buckets(a, today=None)`** from `app/services/larc/workflow.py`
(needs `a.milestones` + `a.device` loaded) for the funnel + enrollment tiles, so the numbers match
the LARC dashboard exactly.

### Backend — router (`backend/app/routers/larc_reports.py`, prefix `/larc/reports`, `Tier.VIEW`)
New router, registered in `app/main.py`. Endpoints mirror surgery/pellet reports:
- `GET /summary?from=&to=&location=&device_type_id=` → all 7 tiles + `period` + a `device_types`
  list (for the filter dropdown).
- `GET /{tile}/rows?from=&to=&location=&device_type_id=&bucket=` → underlying rows (slim serializer).
- Same `rows` with `&format=csv` → `StreamingResponse` `text/csv`. `from`/`to` typed `date` params
  (clean 422); default = current month. Unknown `tile` → 404. `Module.LARC`.

### The 7 tiles (location + device type apply to all; snapshot vs period as marked)
1. **Workflow Funnel** *(snapshot)* — active assignments (status not in billed/cancelled, `is_active`)
   tallied by `assignment_buckets`: `{by_bucket: {<bucket>: n}}` over the ALL_BUCKETS taxonomy
   (needs_benefits, needs_enrollment, needs_fax, awaiting_receipt, received_not_notified,
   appt_scheduled, checked_out, inserted_not_billed, failed_replacement_*, owed, op_* …).
2. **Outstanding Enrollment** *(snapshot)* — the pharmacy-order enrollment pipeline, a focused subset:
   counts of assignments in each of `needs_enrollment` / `needs_fax` / `awaiting_receipt` /
   `received_not_notified` (from `assignment_buckets`). `{by_stage: {...}, total}`.
3. **Insertions Completed** *(period)* — assignments with `inserted_at` in range (status in
   inserted/billed), split by device-type **category** (`larc` vs `office_procedure`), vs the prior
   equal-length period. `{total, by_category, prior_total, prior_from, prior_to, delta}`.
4. **Billing Backlog** *(snapshot)* — assignments `status="inserted"` with `billed_at` null:
   `{count}`.
5. **Owed Patients** *(snapshot)* — open `LarcOwedPatient` rows (reallocation debt) +
   failed-used assignments awaiting a replacement (the `failed_replacement_unrequested` bucket):
   `{owed_count, awaiting_replacement, total}`.
6. **Inventory Health** *(snapshot; location/device-type aware)* — in-stock `LarcDevice` rows
   (`status in ("unassigned","received")`) on-hand by device type & location; devices expiring
   ≤90 days (`expiration_date`); device types below their `reorder_threshold` (count of in-stock per
   device type vs `LarcDeviceType.reorder_threshold`). `{total_on_hand, by_type, expiring, below_reorder}`.
7. **Insertion Outcomes** *(period)* — over `LarcCheckout` rows (the record of each insertion-visit +
   its outcome) with `outcome` set and `requested_at` in range: counts of `success` (`inserted`) vs
   `failed_unused` vs `failed_used`, and the **failure rate** = `(failed_unused+failed_used)/total`
   where total = those three (a `patient_no_show` outcome is excluded from the rate). Filters resolve
   via the checkout's assignment (`device_type_id`) and the assignment's device (`location`).
   `{success, failed_unused, failed_used, total, failure_rate}`.

### Drill-down rows (slim shape)
- Assignment-based tiles: `{assignment_id, chart_number, patient_name, device_type, ownership,
  location, status, source_flow, inserted_at, billed_at}` + a tile-specific column (e.g. `bucket` for
  funnel/enrollment, `category` for insertions, `outcome` for outcomes). `bucket` narrows (a bucket
  for funnel/enrollment, a category for insertions, an outcome key for outcomes).
- `owed_patients`: `LarcOwedPatient` rows `{chart_number, patient_name, device_type, owed_since}`
  (+ the awaiting-replacement assignments under `bucket=awaiting_replacement`).
- `inventory_health`: one row per in-stock device `{our_id, device_type, location, ownership,
  expiration_date}`; `bucket` ∈ a device-type-id, `expiring`, or `below_reorder`.
- `insertion_outcomes`: `LarcCheckout` rows `{checkout_id, chart_number, patient_name, device_type,
  location, outcome, requested_at}`; `bucket` ∈ `success`/`failed_unused`/`failed_used`.
CSV columns mirror the JSON keys.

### Frontend (`frontend/src/pages/LarcReports.jsx`)
Adapts `SurgeryReports.jsx`/`PelletReports.jsx`. Added as a **"Reports"** link in
`frontend/src/components/larc/LarcNav.jsx` (`tier: TIER.VIEW`) + a child route under `/larc` in
`routes.jsx` (`module: M.LARC`). Filter bar: date presets (This Month / Last Month / Last 30 / 90 /
Custom) → `from`/`to`; location select (White Plains / Brandywine / Arlington); device-type select
(from the summary's `device_types`, or `/larc/device-types`). 7-tile responsive grid via one
`useQuery` on `/larc/reports/summary`; each tile/segment clickable → drill-down panel calling
`/{tile}/rows` (with `bucket`) + a **Download CSV** button. MM/DD/YYYY via `fmt.date`; Title Case.

## Testing
- **Backend (TDD), per tile:** seed assignments/devices/owed spanning statuses, source_flows,
  `inserted_at` in/out of range, device types/locations, and stock/expiry; assert each aggregation.
  Funnel + enrollment via `assignment_buckets`; insertions in-range vs prior delta + category split;
  billing backlog; owed count + awaiting-replacement; inventory on-hand/expiring/below-reorder;
  outcome counts + failure rate. Filter application: device-type (on the assignment) and location (via
  the device) narrow; date range affects period tiles only; soft-deleted excluded.
- **Drill-down:** `/{tile}/rows` count matches the headline for a filter set; `bucket` narrows;
  `format=csv` → `text/csv` with header + matching rows. `Tier.VIEW` enforced.
- **Frontend:** `npm run build` clean.
- **Authenticated walk-through** (`backend/tests/test_larc_reports_walkthrough.py`): seed a small set
  → `/summary` returns expected tile numbers → drill a tile → CSV export.

## File structure
- Create `backend/app/services/larc/reports.py`, `backend/app/routers/larc_reports.py` (register in
  `app/main.py`).
- Create `frontend/src/pages/LarcReports.jsx`; wire `routes.jsx` + `components/larc/LarcNav.jsx`.
- Tests: `backend/tests/test_larc_reports_service.py`, `test_larc_reports_router.py`,
  `test_larc_reports_walkthrough.py`.

## Out of scope (YAGNI)
No new tables/columns (compute on request). No scheduled/emailed reports. No charts library beyond
simple bars/numbers. Read-only + drill-down + CSV. The funnel reuses `assignment_buckets` as-is (no
new bucket logic).

## Conventions
MM/DD/YYYY, Title Case, money `$X.XX`; `now_utc_naive()` never `datetime.utcnow()`; lightweight — no
migration; deploy `--project=wwc-solutions` + `--tag=`.
