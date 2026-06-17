# Pellet Reports Design

**Status:** Approved 2026-06-17. A "Reports" tab in the Pellet section mirroring Surgery Reports: a
filter bar (date range + location + provider) over 6 pellet-appropriate tiles, each clickable to a
drill-down list with CSV export.

## Goal
Give the practice the same at-a-glance operational dashboard for the pellet program that Surgery
Reports provides — visit pipeline, insertion throughput, clinical recall, prerequisite readiness,
billing backlog, and inventory health — computed from existing pellet data. `PelletVisit.inserted_at`
is the completion anchor (no migration needed).

## Decisions (from brainstorming)
- **6 tiles** (below): visit status funnel, insertions completed, recall due, prerequisites not
  ready, billing backlog, inventory health.
- **Filters:** date range + location + provider. The range applies to *period* tiles only; *snapshot*
  tiles are "as of now." Location + provider apply to all visit-based tiles.
- **Completion anchor:** `PelletVisit.inserted_at`. `is_historical` visits are excluded from
  operational tiles but **counted** as the "last insertion" for Recall Due.
- **Drill-down + CSV** for every tile; **Tier.VIEW**; default range = current month.

## Architecture

### Backend — aggregation service (`backend/app/services/pellet/reports.py`)
One pure function per tile, `(db, *, date_from, date_to, location, provider)` → dict. A
`_visit_base(db, location, provider)` helper returns a `PelletVisit` query with
`is_historical == False`, plus `location`/`provider` filters when given. Config windows come from
`cfg(db, key)` in `app/services/pellet/settings.py` (`mammo_valid_days`=365, `labs_valid_days`=14,
`require_mammo`, `require_labs`).

Location values: `white_plains`, `brandywine`, `arlington`. Provider is the free-text
`PelletVisit.provider` (options surfaced via `/pellets/picklists`).

### Backend — router (`backend/app/routers/pellet_reports.py`, prefix `/pellets/reports`, `Tier.VIEW`)
New router file, registered in `app/main.py`. Endpoints mirror `surgery_reports`:
- `GET /summary?from=&to=&location=&provider=` → all 6 tiles + `period`.
- `GET /{tile}/rows?from=&to=&location=&provider=&bucket=` → underlying rows (slim serializer).
- Same `rows` with `&format=csv` → `StreamingResponse` `text/csv`.
- `from`/`to` are typed `date` query params (clean 422 on bad input); default = current month
  (1st → today). `tile` ∈ the 6 keys below; unknown → 404.

### The 6 tiles
1. **Visit Status Funnel** *(snapshot)* — `_visit_base` grouped by `status` (new, in_progress,
   inserted, billed, cancelled, rescheduled). `{by_status: {<status>: n}}`.
2. **Insertions Completed** *(period)* — visits with `inserted_at` in `[date_from, date_to]` and
   status in (`inserted`, `billed`), split by `visit_kind` (initial/booster/repeat). Computes the
   immediately-preceding equal-length period for a delta. `{total, by_kind, prior_total, prior_from,
   prior_to, delta}`.
3. **Recall Due** *(snapshot)* — mirrors the existing `recall_is_due` logic in `pellet.py` (~3550-3589)
   so the tile matches the patient roster's "Recall Due" view. For each `PelletPatient` with
   `status="active"`: `last_visit_dt` = the max over the patient's visits of the visit's effective
   date `d = v.inserted_at.date() if v.inserted_at else v.scheduled_date` (includes `is_historical`).
   `interval = recall_interval_months or 4`; `due_date = last_visit_dt + (interval * 30) days`.
   `active` = the patient has an open visit (status not in billed/cancelled with a scheduled_date
   today-or-later, else any open visit). A patient is **overdue** when `due_date < today and not
   active` (exactly the existing `recall_is_due`), **due_soon** when `today <= due_date <= today+30
   and not active`. Patients with no `last_visit_dt` are excluded. Location/provider filter on the
   patient's latest-visit location/provider. `{overdue, due_soon, total}`.
4. **Prerequisites Not Ready** *(snapshot, scheduled ≤14 days)* — `_visit_base` visits with
   `scheduled_date` in `[today, today+14]` and `status in ("new","in_progress")`, whose patient is
   missing a prerequisite. Per-blocker counts:
   - `mammo` → `cfg(require_mammo)` and NOT (`mammo_verified` and `mammo_date` within
     `mammo_valid_days` of today)
   - `labs` → `cfg(require_labs)` and NOT `labs_not_required` and NOT (`labs_verified` and
     `labs_date` within `labs_valid_days`)
   - `consent` → no `PelletConsent` whose `is_valid` property is true (reuse
     `PelletConsent.is_valid`: `status == "signed"` and `expires_at` set and `> now`)
   A visit counts once in `total` if it has ≥1 blocker; per-blocker counts may overlap.
   `{total, by_blocker: {mammo, labs, consent}}`.
5. **Billing Backlog** *(snapshot)* — `_visit_base` visits with `status="inserted"` and `billed_at`
   is null. `{count, total_amount}` (sum `price_amount`).
6. **Inventory Health** *(snapshot; location-aware, provider N/A)* — from `PelletStock`
   (`status="active"`): on-hand doses per location (`{by_location: {<loc>: doses}}`, `total_on_hand`);
   lots expiring ≤90 days with on-hand > 0 (`expiring_lots` count, joining `PelletLot.expiration_date`);
   dose types below their reorder threshold (`below_reorder` count, using
   `PelletDoseType.reorder_thresholds_by_location` vs current on-hand). The `location` filter narrows
   all three; `provider` is ignored for this tile.

### Drill-down rows (slim shape)
- Visit-based tiles (funnel, insertions, prereqs, billing_backlog): `{visit_id, patient_name,
  chart_number, scheduled_date, inserted_at, location, provider, status, visit_kind}` + a
  tile-specific column (e.g. `blockers` for prereqs, `price_amount` for billing_backlog,
  `visit_kind` already present). `bucket` narrows (a status for funnel, a visit_kind for insertions,
  a blocker key for prereqs).
- `recall_due`: patient rows `{patient_id, chart_number, patient_name, last_inserted_at, due_date,
  recall_interval_months}`; `bucket` ∈ `overdue`/`due_soon`.
- `inventory_health`: rows per location×lot `{location, lot_number, dose_type, doses_on_hand,
  expiration_date}`; `bucket` ∈ a location or `expiring`/`below_reorder`.
CSV columns mirror the JSON keys; list cells (`blockers`) join with `"; "`.

### Frontend (`frontend/src/pages/PelletReports.jsx`)
Mirrors `SurgeryReports.jsx`. Added as a **"Reports"** entry in `PelletNav.jsx` + a route in
`routes.jsx` (`tier: TIER.VIEW`, `module: M.PELLETS`). Filter bar: date presets (This Month / Last
Month / Last 30 / 90 / Custom) → `from`/`to`; location select (White Plains / Brandywine / Arlington);
provider select (from `/pellets/picklists`). 6-tile responsive grid via one `useQuery` on
`/pellets/reports/summary`; each tile + segment clickable → drill-down panel calling `/{tile}/rows`
(with `bucket`) + a **Download CSV** button (blob download via `api.get(..., {responseType:'blob'})`).
MM/DD/YYYY via `fmt.date`, money `$X.XX` via `fmt.currency`, Title Case titles/buttons.

## Testing
- **Backend (TDD), per tile:** seed pellet patients/visits/stock spanning statuses, kinds,
  `inserted_at` in/out of range, recall intervals, prerequisite states, and stock/expiry; assert each
  aggregation. Recall-due overdue vs due_soon math; prereq blockers honoring the config windows +
  `require_*`; insertions in-range vs prior delta; billing backlog count/total; inventory on-hand by
  location + expiring + below-reorder. Filter application (location/provider/date). `is_historical`
  excluded from operational tiles but counted for recall.
- **Drill-down:** `/{tile}/rows` row count matches the tile headline for a filter set; `bucket`
  narrows; `format=csv` returns `text/csv` with header + matching rows. Tier VIEW enforced.
- **Frontend:** `npm run build` clean.
- **Authenticated walk-through** (`backend/tests/test_pellet_reports_walkthrough.py`): seed a small
  realistic set → `/summary` returns expected tile numbers → drill a tile → CSV export.

## File structure
- Create `backend/app/services/pellet/reports.py`, `backend/app/routers/pellet_reports.py` (register
  in `app/main.py`).
- Create `frontend/src/pages/PelletReports.jsx`; wire `routes.jsx` + `PelletNav.jsx`.
- Tests: `backend/tests/test_pellet_reports_service.py`, `test_pellet_reports_router.py`,
  `test_pellet_reports_walkthrough.py`.

## Out of scope (YAGNI)
No new tables/columns (compute on request). No subscriptions/revenue tile in v1 (deferred; the
architecture leaves room). No scheduled/emailed reports. No charts library beyond simple bars/numbers.
Read-only + drill-down + CSV.

## Conventions
MM/DD/YYYY, Title Case, money `$X.XX`; `now_utc_naive()` never `datetime.utcnow()`; lightweight —
no migration; deploy `--project=wwc-solutions` + `--tag=`.
