# Surgery Reports Design

**Status:** Approved 2026-06-17. A new "Reports" tab in the Surgery page: a filter bar (date range +
facility + surgeon) over a grid of 6 operational/financial tiles. Each tile is clickable to a
drill-down list of the underlying surgeries, with CSV export.

## Goal
Give the practice an at-a-glance operational dashboard for surgery: where cases are in the pipeline,
which upcoming cases aren't ready, throughput, cycle time, the payment-posting backlog, and block
utilization — all computable from data the system already stores (the surgery record carries a
timestamp for every workflow step).

## Decisions (from brainstorming)
- **v1 = 6 tiles** (below). **Filters:** date range + facility + surgeon. **Drill-down:** clickable
  tiles → underlying surgery list. **Export:** CSV of a tile's rows.
- **Completion** anchored on `Surgery.completed_at`; **lead time** = `scheduled_date − created_at`.
- **Snapshot vs period:** the date range applies only to *period* tiles; *snapshot* tiles are always
  "as of now." Facility + surgeon filters apply to ALL tiles.
- **Default range:** current month (1st → today). **Tier:** `VIEW` (same as viewing surgeries).

## Architecture

### Backend — aggregation service (`backend/app/services/surgery/reports.py`)
One pure function per tile, signature `(db, *, date_from, date_to, facility, surgeon)` returning a
dict. Keeps logic out of the already-large `surgery.py`. A `_base_query(db, facility, surgeon)` helper
applies the shared filters (`Surgery.selected_facility == facility` when given;
`Surgery.surgeon_primary == surgeon` when given). All money clamped/financial values formatted at the
edge, not here.

Facility codes: the 4 in `SURGERY_FACILITY_VALUES` (`medstar`, `crmc`, `office`,
`wwc_office_white_plains`). Surgeon = the `surgeon_primary` string (options come from picklists).

### Backend — router (`backend/app/routers/surgery_reports.py`, prefix `/surgery/reports`, `Tier.VIEW`)
New router file (registered in `app/main.py` next to the other surgery routers), so `surgery.py`
doesn't grow further.
- `GET /summary?from=&to=&facility=&surgeon=` → all 6 tiles in one payload:
  `{period: {from, to, prior_from, prior_to}, status_funnel, not_ready, completed, cycle_time,
    posting_backlog, utilization}`.
- `GET /{tile}/rows?from=&to=&facility=&surgeon=&bucket=` → the underlying rows for a clicked tile
  (and optional `bucket` sub-segment, e.g. a status name, a blocker key, or a facility). Returns a
  slim row shape (see Drill-down). `tile` ∈
  `{status_funnel, not_ready, completed, cycle_time, posting_backlog, utilization}`.
- Same `rows` endpoint with `&format=csv` → `StreamingResponse` `text/csv` (filename
  `surgery-<tile>-<from>_<to>.csv`), same columns as the JSON rows.
- Query params: `from`/`to` are `YYYY-MM-DD` (parsed to dates; default = current month start → today
  when omitted); `facility`/`surgeon` optional; invalid `tile`/`bucket` → 404/422.

### The 6 tiles
1. **Status funnel** *(snapshot)*: count of surgeries grouped by status, mapped to the taxonomy
   labels (Incomplete, New, Benefits Check, Pre-Surgery, Post-Surgery, Unresponsive, Hold). Canceled
   and Completed reported as separate counts (not part of the active funnel). Uses the status→label
   map already used elsewhere. Facility/surgeon filters apply.
2. **Not ready, surgery ≤14 days** *(snapshot, next 14 days from today)*: surgeries with
   `scheduled_date` in `[today, today+14]` and status not in (`cancelled`, `completed`) where any
   readiness gate is incomplete. **Reuse the canonical per-step completion logic** —
   `_state(s, key)` in `app/services/surgery/step_engine.py` (returns `"done" | "todo" |
   "in_progress" | "n/a"`) — so this matches the step cards exactly rather than re-deriving
   conditions. A step is a **blocker** when `_state(s, key) in ("todo", "in_progress")` (`"done"` and
   `"n/a"` are not blockers). Per-blocker counts for the keys: `benefits`, `consents`, `prior_auth`,
   `clearance`, `device`, `labs`. A surgery counts once in the total if it has ≥1 blocker;
   per-blocker counts may overlap. Import `_state` (or add a thin public wrapper in `step_engine.py`
   if preferred over importing the underscore-prefixed helper). Facility/surgeon apply; ignores the
   date-range filter (always next 14 days).
3. **Completed this period vs prior** *(period)*: count of surgeries with `completed_at` in
   `[date_from, date_to]`, split by `procedure_classification`. Also computes the immediately
   preceding equal-length period `[prior_from, prior_to]` for comparison (total + delta).
4. **Cycle time + reschedule rate** *(period)*: over surgeries with `completed_at` in range:
   `avg_lead_days` = mean of `(scheduled_date − created_at)` in days (skip rows missing either);
   `reschedule_rate` = share with `reschedule_count > 0`; `avg_reschedules` = mean `reschedule_count`.
5. **Payment-posting backlog** *(snapshot)*: paid Stripe `SurgeryPayment`s not yet posted
   (`posted_to_modmed_at` is null), reusing the existing `_stripe_only_filter` predicate (exclude
   `manual_offset`). Returns `count`, `total_amount` (sum `amount_paid`), and `oldest_age_days` (from
   the oldest `paid_at`). Facility/surgeon filters apply via the joined `Surgery`.
6. **Facility / block utilization** *(period)*: for `BlockDay`s with `block_date` in range (filtered
   to `facility` when given), `booked` = count of `SurgerySlot`s on those days; `capacity` = sum of
   each block day's slot capacity (slots-per-day from `capacity_rules(db)` by facility/`block_kind`,
   same source the materializer uses); `utilization_pct` = `booked / capacity` (guard divide-by-zero).
   Reported overall and per facility.

### Drill-down rows (slim shape)
For surgery-based tiles: `{surgery_id, surgery_number, chart_number, patient_name, surgeon_primary,
selected_facility, scheduled_date, status, status_label}` plus a tile-specific column
(e.g. `blockers: [...]` for not-ready, `classification` + `completed_at` for completed, `lead_days`
for cycle_time). For `posting_backlog`: the payment rows (reuse the Payment Posting row shape:
`{id, chart_number, patient_name, amount_paid, paid_at, confirmation, age_days}`). For `utilization`:
one row per block day `{facility, block_date, block_kind, booked, capacity}`. CSV columns mirror the
JSON keys (flatten list columns like `blockers` to a comma-joined string).

### Frontend (`frontend/src/pages/SurgeryReports.jsx`)
- Added as a **"Reports"** tab in the Surgery page tab set (mirror how `SurgeryPaymentPosting` is
  wired). Title Case tab + headings.
- **Filter bar:** date-range presets (This Month / Last Month / Last 30 Days / Last 90 Days / Custom)
  resolving to `from`/`to`; facility dropdown (the 4 codes → labels MedStar / CRMC / Office / WWC
  Office White Plains); surgeon dropdown (from `/surgery/picklists` `surgeons`). "All" option on each.
- **6-tile responsive grid** via one `useQuery(['surgery-report-summary', filters])` →
  `GET /surgery/reports/summary`. Each tile shows the headline number + a small breakdown
  (funnel rows, blocker chips, classification split, the two cycle-time figures, backlog count/$/age,
  utilization % per facility).
- **Drill-down:** clicking a tile (or a segment within it) opens a panel/modal that fetches
  `GET /surgery/reports/{tile}/rows` (with the segment as `bucket`) and lists the rows; a
  **Download CSV** button hits the same endpoint with `format=csv` and triggers a file download.
- react-query refetches on filter change; `fmt.date()` (MM/DD/YYYY) and `$X.XX` money; empty/loading
  states per tile.

## Testing
- **Backend (TDD), per tile:** seed surgeries spanning statuses, classifications, `completed_at` in
  and out of range, varied readiness gates, reschedules; assert each aggregation. Specifically:
  status-funnel counts by label; not-ready total + per-blocker (incl. a fully-ready ≤14-day surgery
  excluded, and one >14 days excluded); completed in-range vs prior-period delta; cycle-time mean +
  reschedule rate; posting backlog count/total/oldest (manual_offset excluded); utilization
  booked/capacity (incl. divide-by-zero → 0%). Filter application: facility and surgeon narrow every
  tile; date range affects period tiles only.
- **Drill-down:** `/{tile}/rows` row count matches the tile's headline for a given filter set;
  `bucket` narrows correctly (e.g. `status_funnel?bucket=hold`); `format=csv` returns `text/csv` with
  a header row + matching row count.
- **Tier:** VIEW can read; the endpoints are gated `Tier.VIEW`.
- **Frontend:** `npm run build` clean.
- **Authenticated walk-through** (`backend/tests/test_surgery_reports_walkthrough.py`): seed a small
  realistic set → `GET /summary` returns the expected tile numbers → drill a tile via `/rows` → CSV
  export returns the rows.

## File structure
- Create `backend/app/services/surgery/reports.py` — aggregations (one function per tile + helpers).
- Create `backend/app/routers/surgery_reports.py` — endpoints; register in `app/main.py`.
- Create `frontend/src/pages/SurgeryReports.jsx`; wire a "Reports" tab into the Surgery page.
- Tests: `backend/tests/test_surgery_reports_service.py`, `test_surgery_reports_router.py`,
  `test_surgery_reports_walkthrough.py`.

## Out of scope (YAGNI)
- No new persisted tables or materialized/cached aggregates — tiles compute on request (the data
  volume is small). No scheduled/emailed reports. No charts library beyond simple bars/numbers
  (reuse existing styling). No per-CPT revenue analytics, payer-mix, or billed-coverage tiles in v1
  (deferred; the architecture leaves room to add tiles). No editing from Reports — read-only +
  drill-down + CSV.

## Conventions
MM/DD/YYYY dates, Title Case tab/headers/buttons, money `$X.XX`; `now_utc_naive()` (never
`datetime.utcnow`); no secrets in source; deploy `--project=wwc-solutions` + `--tag=`.
