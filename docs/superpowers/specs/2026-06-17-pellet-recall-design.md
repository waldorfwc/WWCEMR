# Pellet Recall Design

**Status:** Approved 2026-06-17. A dedicated "Recall" page in the Pellets nav: a worklist of pellet
patients due for re-insertion, where clicking a row opens the same rich call-workflow detail modal as
the WWE Recall module (patient card, insertion history, caller script, claim, dial, log call outcome,
attempts, history). Reuses the existing recall engine; gated by pellet permissions.

## Goal
Give staff a pellet-specific recall worklist + call workflow without rebuilding the call-center
machinery. Pellet patients overdue for re-insertion (per the existing `recall_is_due` rule) are
materialized into the existing `RecallEntry`/call-log engine and worked through the same
claim → dial → log-outcome flow, surfaced under Pellets and gated by the pellet module.

## Decisions (from brainstorming)
- **Criterion:** the existing pellet `recall_is_due` (last insertion + `recall_interval_months`×30
  days < today AND no open visit). Matches the patient roster's "Recall Due" view and the Reports
  "Recall Due" tile, so all three agree.
- **Modal:** full parity with the WWE recall detail (claim/lock, dial, log outcome + attempts +
  history, caller script) — by reusing the recall engine.
- **Permissions:** the page + endpoints are gated by `Module.PELLETS` (not `Module.RECALL`).
- **Approach A — reuse the recall engine** (not a parallel pellet system).

## Architecture

### Backend — reuse `RecallEntry`, gate by pellet
`RecallEntry` is generic call-center plumbing keyed on `chart_number` + free-text `recall_type`
(`recall_call_logs` for attempts/views, claim/lock fields, `attempts`/`last_outcome` rollup). The
WWE Smartsheet importer updates only `recall_type="Est - Well-Woman Exam"` rows in place and deletes
nothing, so injected pellet rows are never touched. We tag pellet rows `recall_type="Pellet
Re-insertion"`.

1. **Sync service** `app/services/pellet/recall_sync.py` → `materialize_pellet_recalls(db) -> dict`:
   - For each `PelletPatient` with `status="active"`, reuse the existing per-patient helper
     `_patient_view_extras(p, today)` in `app/routers/pellet.py` (it returns `recall_is_due` +
     `recall_due_date` — the same computation the roster's "Recall Due" view uses; import it, or move
     it to `app/services/pellet/` if cleaner). When `recall_is_due`: **upsert** a `RecallEntry`
     keyed `(chart_number, "Pellet Re-insertion")` with `recall_due`=`recall_due_date`,
     `patient_name`, `dob`, `phone` (cell preferred), `status="active"`. Update in place — never
     reset `attempts`/claim/`last_outcome` on an existing row (mirrors the WWE importer).
   - For existing `"Pellet Re-insertion"` entries whose patient is NO LONGER due (scheduled a future
     visit, got inserted, or went inactive): set `status="completed"`.
   - Idempotent. Returns `{created, updated, completed}`. Runs on a daily cron (added to the
     `fax_poller` scheduler, cross-instance-locked via `claim_cron_run`) and on demand via
     `POST /pellets/recall/sync`.

2. **Shared recall actions.** The recall claim/release/dial/log-outcome/call-attempted logic is
   currently inline in the `app/routers/recalls.py` handlers. Extract the bodies into reusable
   functions in `app/services/recall/actions.py` — `claim(db, entry, user)`, `release(db, entry,
   user)`, `dial(db, entry, user)`, `log_call_attempted(db, entry, user)`, `log_outcome(db, entry,
   payload, user)` — and have the existing recall handlers call them (thin wrappers). The WWE module
   behavior is unchanged (covered by existing recall tests). This lets the pellet router reuse the
   exact same actions under pellet gating.

3. **Pellet recall router** `app/routers/pellet_recall.py` (prefix `/pellets/recall`,
   `Module.PELLETS`):
   - `POST /sync` (WORK) → `materialize_pellet_recalls(db)`.
   - `GET ""` (VIEW) → list active `"Pellet Re-insertion"` entries (reuse `_entry_to_dict`),
     newest-due first; supports `search`.
   - `GET /{recall_id}` (VIEW) → detail. Reuse the recall detail assembly, but the **history card is
     the patient's pellet insertion history** (`PelletVisit` newest-first: date, location, provider,
     dosage) instead of `WWEVisit`; include `recall_type`, recall-due, attempts, the call-log
     history, and the **pellet caller script** (`cfg(db, "recall_caller_script")` — a new pellet
     setting with starter copy). A 404 if the entry isn't a pellet recall.
   - `POST /{recall_id}/claim`, `DELETE /{recall_id}/claim`, `POST /{recall_id}/call-attempted`,
     `POST /{recall_id}/dial`, `POST /{recall_id}/outcome` (WORK) → load the `RecallEntry`, assert
     `recall_type=="Pellet Re-insertion"`, then call the shared `actions.*` function. Same payloads
     as the recall endpoints (outcome = `{outcome, notes, ...}`).
   Outcome taxonomy: reuse the existing recall outcomes config (the same outcomes —
   reached/voicemail/scheduled/declined/etc. — apply to pellet recalls).

### Frontend
- New nav link **"Recall"** in `frontend/src/components/pellet/PelletNav.jsx` (`tier: TIER.WORK`),
  next to the others, + a route `/pellets/recall` → `PelletRecall.jsx` in `routes.jsx`
  (`module: M.PELLETS`).
- `PelletRecall.jsx`: on mount calls `POST /pellets/recall/sync`, then lists
  `GET /pellets/recall` (chart, name, phone, recall-due, last insertion, attempts, claim state).
  Clicking a row opens a **`PelletRecallDetail.jsx`** modal built to the same layout as the WWE
  recall detail (the screenshot: patient card → insertion-history card → Caller Script → Log Call
  Outcome → History). Building a dedicated pellet modal (rather than retrofitting the WWE one to take
  configurable endpoints + history source) keeps the working WWE page untouched. It calls the
  `/pellets/recall/{id}/...` endpoints for claim/dial/outcome. Dates MM/DD/YYYY; Title Case.

## Testing
- **Backend:** `materialize_pellet_recalls` creates a `RecallEntry` for a due pellet patient, updates
  in place without resetting attempts, and completes an entry once the patient schedules/inserts;
  idempotent; WWE recall rows untouched. The pellet router: list filters to the pellet type; detail
  returns pellet insertion history + caller script + 404 for a non-pellet entry; claim/outcome on a
  pellet entry go through the shared action and write a call log + bump attempts. The recall-actions
  extraction leaves the existing recall tests green. Endpoints gated `Module.PELLETS`.
- **Frontend:** `npm run build` clean.
- **Authenticated walk-through** (`backend/tests/test_pellet_recall_walkthrough.py`): seed an overdue
  pellet patient → `POST /sync` materializes the entry → `GET /pellets/recall` lists it → `GET
  /{id}` shows insertion history → `POST /{id}/outcome` logs a call → attempts bumped + history row.

## File structure
- Create `backend/app/services/pellet/recall_sync.py`, `backend/app/routers/pellet_recall.py`,
  `backend/app/services/recall/actions.py` (extracted shared logic).
- Modify `backend/app/routers/recalls.py` (handlers delegate to `actions.*`), `app/main.py`
  (register pellet_recall), `app/services/fax_poller.py` (daily sync cron),
  `app/services/pellet/settings.py` (`recall_caller_script` default).
- Create `frontend/src/pages/PelletRecall.jsx` (+ `PelletRecallDetail.jsx` if needed); modify
  `routes.jsx`, `components/pellet/PelletNav.jsx`.
- Tests: `test_pellet_recall_sync.py`, `test_pellet_recall_router.py`, `test_pellet_recall_walkthrough.py`.

## Out of scope (YAGNI)
- No change to the WWE recall page/behavior beyond the mechanical action extraction. No new outcome
  taxonomy (reuse the existing one). No separate pellet recall settings page (the caller script is a
  pellet setting; outcomes come from the shared recall config). No bulk-call/auto-dialer.

## Conventions
MM/DD/YYYY, Title Case, `now_utc_naive()` never `datetime.utcnow()`; lightweight migration only if a
column is needed (none expected — `RecallEntry` already has the fields); cron needs cross-instance
lock; deploy `--project=wwc-solutions` + `--tag=`.
