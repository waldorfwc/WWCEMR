# Phase 1 — Document Retrieval & Fax-to-EMA

**Date:** 2026-04-19
**Project:** wwc-era-project
**Depends on:** Phase 0 UI redesign (already shipped on `main`)

## Goal

Make the "send patient docs from the PrimeSuite export to ModMed EMA" workflow fast, batched, and tracked. A clinician opens a patient chart, ticks the documents that need to go to EMA, chooses how to group them, clicks send, and later sees a clear per-doc status (sent / delivered / failed) with a retry path on failures.

This phase builds on existing infrastructure: document listing, RingCentral fax sending, and the audit log all work. The gap we're filling is **state**: today every fax is fire-and-forget with no persistent log, no delivery confirmation, and no batch UX.

## Workflow (the user's view)

1. Open any patient chart (`/chart/:chartNumber`).
2. Each document row now has a checkbox on the left and a fax-status chip on the right.
3. Tick one or more docs. The "**Fax N docs to EMA →**" action bar activates above the doc list.
4. Click it. A modal opens, pre-filled with the default EMA fax number (editable), a grouping choice (default: Separate), and an auto-generated cover note.
5. Click Send. Backend writes a batch of FaxLog rows, calls RingCentral, and returns. The chart view shows each doc's chip as `⟳ Sending`.
6. A background poller and a 30-second chart-level refetch update the chips to `✓ Sent` (RingCentral accepted), `✓ Delivered` (confirmed), or `✗ Failed — retry`.
7. A new `/fax-log` page lists all faxes with filters (status, chart, date). The Dashboard's "Recent faxes to EMA" card is wired to real data.

## Non-goals

- Multi-chart batch send (one chart at a time in Phase 1).
- Patient consent tracking (flagged in survey, not in scope).
- Per-provider destination routing (the editable dest field covers one-offs).
- A full Settings UI for editing `ema_default_fax` (env/DB seed for now).
- Frontend test framework (continue manual testing; backend gets pytest coverage).

## Data model

New `FaxLog` model in `backend/app/models/fax_log.py`:

```
id                     : GUID, PK
chart_number           : str, indexed
doc_ids                : JSON array of GUID strings  # one or more docs in this fax
grouping_mode          : enum ('separate','combined','by_type')
dest_fax               : str                         # normalized E.164 or digits-only
ringcentral_message_id : str, nullable
status                 : enum ('queued','sent','delivered','failed') default 'queued'
sent_at                : datetime, default now()
last_checked_at        : datetime, nullable
delivered_at           : datetime, nullable
error                  : text, nullable
sent_by                : str (user email from session)
retry_of               : GUID, FK fax_logs.id, nullable  # links retries to the original fax
created_at / updated_at
```

Indexes: `(chart_number, sent_at desc)`, `(status, last_checked_at)` (for the poller).

Practice config — single row in a new `PracticeConfig` table (or a JSON file seeded on migration; table is tidier):

```
key    : str PK           # e.g. 'ema_default_fax', 'ema_fax_label'
value  : str
```

Seed values: `ema_default_fax = '2402522141'`, `ema_fax_label = 'ModMed EMA'`.

No other schema changes. Existing `PatientDocument`, `Claim`, `AuditLog`, etc. stay as-is.

## API changes

### New: `POST /api/fax/send-batch`

Request:
```json
{
  "chart_number": "12345",
  "doc_ids": ["<uuid>", "<uuid>", "<uuid>"],
  "dest_fax": "2402522141",
  "grouping_mode": "separate",
  "cover_text": "Patient: Adams, Pamella\nDOB: 1985-02-14\nChart #12345"
}
```

Response (happy):
```json
{
  "batch_id": "<uuid>",
  "faxes": [
    {"fax_log_id": "<uuid>", "doc_ids": ["<uuid>"], "ringcentral_message_id": "12345", "status": "sent"},
    {"fax_log_id": "<uuid>", "doc_ids": ["<uuid>"], "ringcentral_message_id": "12346", "status": "sent"},
    {"fax_log_id": "<uuid>", "doc_ids": ["<uuid>"], "status": "failed", "error": "document not found"}
  ]
}
```

Rules:
- `grouping_mode="separate"` → N faxes (length = len(doc_ids)).
- `grouping_mode="combined"` → 1 fax with merged PDF.
- `grouping_mode="by_type"` → M faxes grouped by each doc's `doc_type`.
- Every fax gets its own `FaxLog` row before RingCentral is called, so we can always reconstruct what was attempted.
- Per-fax errors do NOT abort the batch — other faxes proceed, the failed row gets `status="failed"` with the reason.
- Credential-level failures (RingCentral token expired, service unreachable) return HTTP 502 with no FaxLog rows written.
- Writes one `audit_logs` entry `FAX_BATCH_SENT` plus one per-fax audit entry (FAX_SENT or FAX_FAILED) keeping the existing audit pattern.

### New: `GET /api/fax/recent?limit=5`

Returns the N most recent FaxLog rows for the Dashboard card:

```json
[
  {
    "id": "<uuid>", "chart_number": "12345", "patient_name": "Adams, Pamella",
    "status": "delivered", "sent_at": "2026-04-19T09:12:00Z", "dest_fax": "2402522141",
    "doc_count": 1
  }
]
```

Shape matches what `Dashboard.jsx` already assumes. Kills the 404 the dashboard is currently logging.

### New: `GET /api/fax/by-chart/{chart_number}`

Returns FaxLog rows for a single chart so the chart view can show status chips next to each doc. Each row lists its `doc_ids` so the frontend can map `doc_id → most-recent-fax-status`.

### New: `POST /api/fax/retry/{fax_log_id}`

Retries a failed fax using the same doc_ids / dest / grouping / cover text. Writes a new FaxLog row (links back to the original via `retry_of` nullable FK for audit). Returns the same shape as `send-batch`.

### New: `GET /api/fax-log?status=&chart=&from=&to=&page=`

Paginated FaxLog listing for the `/fax-log` page.

### Replace: existing `POST /api/fax/send`

Phase 1 keeps the route but internally routes to `send-batch` with `doc_ids=[one]`. Callers (today: `PatientChart.jsx` fax modal) get the benefits of FaxLog tracking automatically. Old response shape preserved for compatibility.

### Unchanged

All `/api/documents/*` and `/api/chart/*` endpoints stay as-is.

## Background poller

New `backend/app/services/fax_poller.py` — runs every 2 minutes, started in `app.main:lifespan`. For each FaxLog row where `status IN ('queued','sent')` and `sent_at > now() - interval '1 hour'`:

1. Call RingCentral `GET /message-store/{message_id}`.
2. Map RingCentral state → our state: `Queued/Sending → sent`, `Sent/Delivered → delivered`, anything with an error prefix → `failed`.
3. Update `status`, `last_checked_at`, and `delivered_at` (on terminal success) or `error` (on failure).
4. Write `FAX_DELIVERED` or `FAX_FAILED` audit entry on state transitions.

After an hour of polling, stale `sent` rows stay in `sent` (RingCentral's final status isn't guaranteed; that's "delivered enough" for our purposes).

Implementation note: use `APScheduler` (`BackgroundScheduler` running alongside uvicorn) — simple, in-process, fine for this load (≤ dozens of rows per poll cycle). APScheduler goes in `requirements.txt`. The scheduler starts in the existing `lifespan` context manager.

## Frontend changes

### `PatientChart.jsx`

Today: lists intake docs and PrimeSuite docs, each with a single "Fax" button that opens a per-doc fax modal.

After Phase 1:

- Each doc row has a checkbox and a status chip. Chip reads from a React Query hook keyed by `fax-by-chart/{chart_number}` that auto-refreshes every 30s when any row is non-terminal.
- A persistent action bar above each doc section: "Fax N docs to EMA" (disabled until ≥1 checkbox ticked). Includes a "Select all unsent" helper link.
- The old single-doc modal is replaced by a new `FaxBatchModal` component with:
  - Destination fax (text input, pre-filled from `PracticeConfig.ema_default_fax`)
  - Grouping mode (radio: Separate / Combined / By type, default Separate)
  - Cover text (textarea, auto-filled)
  - Send button that fires `POST /fax/send-batch` and shows per-fax results inline before closing.
- Status chip variants: empty / `⟳ Sending` (plum) / `✓ Sent` (muted green) / `✓ Delivered` (success green) / `✗ Failed — retry` (danger, clickable → retry endpoint). Based on the same palette as the rest of the redesign.
- Existing single-doc fax button stays (calls the same modal with one doc pre-selected) for backwards-compatible muscle memory.

### `Dashboard.jsx`

No code change. The "Recent faxes to EMA" card starts showing real data once `/api/fax/recent` exists.

### New page: `/fax-log`

Simple table: chart, patient, doc count, grouping, dest, status, sent-at, sent-by. Filters: status (dropdown), chart (text), date range. Paginated (50 per page). Retry button on failed rows. Same palette/table classes as the rest of the app — no net-new design.

Top nav gains a "Fax log" entry (replaces or sits alongside "Audit" depending on space — replacing Appeals would be too destructive; we'll sit it between Import and Audit).

### Minor: `utils/api.js` helpers

Add `fmt.faxStatus(status)` for chip labels, `fmt.faxDate(ts)` (short `MM/dd h:mm a`). No structural changes.

## Config

New env variables / DB seeds:

- `PRACTICE_CONFIG.ema_default_fax = '2402522141'` (DB row seeded by migration)
- `PRACTICE_CONFIG.ema_fax_label = 'ModMed EMA'`
- `FAX_POLL_INTERVAL_MINUTES = 2` (env, default 2)
- `FAX_POLL_MAX_AGE_MINUTES = 60` (env, default 60)

No changes to RingCentral credential loading.

## Files touched

Created:
- `backend/app/models/fax_log.py`
- `backend/app/models/practice_config.py`
- `backend/app/routers/fax_batch.py` *(new router file; keeps existing `fax.py` untouched except for delegating `/send` to the batch path)*
- `backend/app/services/fax_poller.py`
- `backend/app/services/pdf_merge.py` (wraps `pypdf` for the combined mode)
- `backend/scripts/seed_practice_config.py` (one-shot seeder; idempotent)
- `backend/tests/test_fax_batch.py`
- `backend/tests/test_fax_poller.py`
- `backend/tests/test_fax_recent.py`
- `frontend/src/pages/FaxLog.jsx`
- `frontend/src/components/FaxBatchModal.jsx`
- `frontend/src/components/FaxStatusChip.jsx`
- `frontend/src/hooks/useFaxByChart.js`

Modified:
- `backend/app/main.py` — register new router + start poller in lifespan
- `backend/app/routers/fax.py` — refactor existing `/send` to delegate to `send-batch`
- `backend/requirements.txt` — `pypdf`, `apscheduler`
- `frontend/src/pages/PatientChart.jsx` — checkboxes, action bar, modal integration, status chips
- `frontend/src/App.jsx` — new `/fax-log` route
- `frontend/src/components/layout/TopNav.jsx` — add "Fax log" link

No frontend destructive changes to unrelated pages.

## Verification

- `cd backend && pytest tests/test_fax_*.py -v` — all pass. Mocks RingCentral via `respx` (adds as dev dep).
- Manual backend: start stack, curl `/api/fax/send-batch` with a fake doc id → 404 doc-not-found error path. Real doc id → creates FaxLog row + (if creds present) RingCentral call.
- Manual frontend: open a chart, tick 2 docs, send with each grouping mode in turn. Observe chips progress. Wait 3 minutes, confirm poller flips `sent → delivered`. Retry a failed fax. Visit `/fax-log`, filter by status.
- Dashboard: "Recent faxes to EMA" shows the latest row.

## Open questions

None blocking. One nice-to-have for Phase 5: per-chart "suggested doc set" ("all recent OV notes + latest insurance card") — out of scope here.
