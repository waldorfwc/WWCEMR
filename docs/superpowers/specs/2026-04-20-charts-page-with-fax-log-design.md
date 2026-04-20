# Charts Page with Inline Fax Log

**Date:** 2026-04-20
**Project:** wwc-era-project
**Depends on:** Phase 1 fax-to-EMA (already shipped)

## Goal

Reshape the Charts page so staff can see at a glance (a) which patients have been faxed to EMA recently, (b) exactly what was sent, and (c) by whom — preventing duplicate faxes during the PrimeSuite-to-ModMed migration push. Secondary: reorder the top nav and add practice-wide document totals at the top.

Visual reference: `.superpowers/brainstorm/85255-*/content/charts-page-v2.html`

## Scope

1. **TopNav reorder:** `Dashboard → Charts → A/R → Claims → Denials → Appeals → Import → Audit`. The standalone "Fax log" entry is removed — its content lives on the Charts page now.
2. **Charts page (`/documents` route) layout:**
   - **Header:** "Patient Charts" + totals `942,043 documents · 37,165 patients` pulled from `/api/documents/index/status`.
   - **Left pane (280px):** patient list, unchanged in function but with:
     - DOB added to each row and made searchable (name OR chart # OR DOB).
     - A compact last-sent-date chip on each row: `✓ 4/20` (bright green if faxed today, muted plum otherwise, `—` if never).
   - **Right pane (flex-1):** recent fax log table with columns: Sent, Patient, DOB, Chart, Docs, Doc types, Dest, Status, Sent by. Filters: status dropdown, window dropdown (Last 7 / 30 / 90 days). Pagination if > 50 rows.
3. **No change** to the per-patient chart detail page (`/chart/:chartNumber`) — the batch-fax flow built in Phase 1 remains as-is.

## Non-goals

- Patient-level fax stats tile on the Dashboard (Phase 0's "Recent faxes to EMA" card already handles that).
- Filter UI for the left-pane patient list beyond search (no status-dropdown for patients).
- Editing `sent_by` after-the-fact (only the current user at send time is captured).
- Surface the standalone paginated fax-log page; it's replaced.

## Required backend work

### 1. Populate `FaxLog.sent_by`

Currently NULL on every row — flagged in Phase 1 final review. Add a shared `get_current_user` FastAPI dependency that reads the session token (`auth.py` already validates session tokens; extend it) and returns the user's email. Inject into `fax_batch.send_batch` and `fax_batch.fax_retry`; set `FaxLog.sent_by = current_user.email` before flushing. Historical rows stay NULL — the frontend renders "—" for missing values.

### 2. Extend `GET /api/fax/recent`

Add to each returned row:
- `dob` — from `PatientDirectory.dob`, or NULL.
- `doc_types` — array of distinct `PatientDocument.doc_type` values for the fax's `doc_ids` (joined at serialize time; OK because N≤5). Empty list if none resolve.
- `sent_by` — already on the model; surface it.

Also support `?window=7|30|90` (days) and `?status=` filters on this same endpoint so the right-pane can reuse it. Default limit stays 5 for the Dashboard; Charts page passes `limit=50`.

### 3. Add DOB to `/api/documents/patients`

The existing endpoint returns `chart_number, patient_name, doc_count`. Add `dob` (from `PatientDirectory`). Add `search` query param semantics: match against `patient_name`, `chart_number`, OR `dob` (ISO string). The existing search already hits patient_name + chart_number; extend to include DOB.

### 4. New `GET /api/fax/chart-summary`

Returns `[{chart_number, fax_count, last_sent_at}]` for every chart with any FaxLog row. Used by the patient list to display the per-row chip. One query, grouped by `chart_number`. Cheap even at 37k charts because we only return charts with activity.

## Required frontend work

### 1. `TopNav.jsx` — reorder `nav` array

```js
const nav = [
  { to: '/',          label: 'Dashboard' },
  { to: '/documents', label: 'Charts' },
  { to: '/ar',        label: 'A/R' },
  { to: '/claims',    label: 'Claims' },
  { to: '/denials',   label: 'Denials' },
  { to: '/appeals',   label: 'Appeals' },
  { to: '/import',    label: 'Import' },
  { to: '/audit',     label: 'Audit' },
]
```

Also drop the `/fax-log` route from `App.jsx` and delete `pages/FaxLog.jsx` entirely (its logic is absorbed into the right-pane component).

### 2. Rewrite `pages/Documents.jsx`

Single file rewrite. Components:
- `<ChartsHeader totals={…}>` — title + totals numbers.
- `<PatientList search={…} data={…} faxSummaries={…} selectedChart={…} onSelect={…}>` — existing function + DOB row + per-row chip (reads `faxSummaries[chart_number]`).
- `<FaxLogPane faxes={…}>` — right-pane table. Columns as specified. Window + status filters local to the component.

### 3. New `useChartFaxSummary` hook

Thin wrapper around `useQuery(['fax-chart-summary'], …)` that returns `{ [chart_number]: { fax_count, last_sent_at } }`. No auto-refresh (staleTime 2 minutes is plenty for a migration workflow).

### 4. Chip coloring rules

- `last_sent_at` is today → bright green (`bg-green-100 text-green-800`)
- `last_sent_at` is older → muted plum (`bg-plum-100 text-plum-700`)
- Never faxed → dash, gray, opacity-45

## Files touched

**Created:**
- `backend/tests/test_fax_chart_summary.py`
- `frontend/src/hooks/useChartFaxSummary.js`
- `frontend/src/pages/Documents.jsx` (full rewrite replacing the existing file)
- `frontend/src/pages/charts/FaxLogPane.jsx` (extracted component)

**Modified:**
- `backend/app/routers/fax_batch.py` — extend `/fax/recent`, add `/fax/chart-summary`
- `backend/app/routers/fax_batch.py` / `fax.py` — wire `sent_by` from session (shared auth dep)
- `backend/app/routers/auth.py` — export a `get_current_user` dependency
- `backend/app/routers/documents.py` — add DOB to `/patients`, search by DOB
- `backend/tests/test_fax_recent.py` — extend tests for `dob`, `doc_types`, `sent_by`, window/status filters
- `frontend/src/App.jsx` — drop `/fax-log` route + import
- `frontend/src/components/layout/TopNav.jsx` — reorder + drop Fax log

**Deleted:**
- `frontend/src/pages/FaxLog.jsx`

## Verification

- `pytest backend/tests/` — all prior tests + new chart_summary + extended recent tests PASS.
- `npm run dev` from repo root, navigate to `/documents`:
  - Header shows totals.
  - Patient list shows DOB + date chips.
  - Search by DOB `1985-02-14` narrows the list.
  - Right pane shows recent faxes with all 9 columns, filters work.
  - Dashboard's "Recent faxes to EMA" card still works (same endpoint, backward-compat default).
- Navigation: `Dashboard | Charts | A/R | Claims | …` — Fax log entry gone.

## Open questions

None blocking.
