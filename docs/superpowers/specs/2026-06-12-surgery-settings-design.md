# Surgery Settings + Steps Engine + Dead-Code Cleanup — Design

Date: 2026-06-12
Status: Approved scope, pending spec review
Owner: Oliver Cooke

## Goal

Three coupled deliverables:

1. **`/surgery/settings`** — a new settings page (gear button on the Surgery
   dashboard, MANAGE tier) that makes the surgery module's hardcoded business
   values editable at runtime.
2. **Server-side Steps engine** — finish the half-done milestone → steps
   transition. Steps (computed from live surgery data) become the single
   source of truth for workflow progress AND for the dashboard's
   behind-schedule / Critical Alerts logic. Milestone reads are retired.
3. **Dead-code cleanup** — remove the DocuSign integration (BoldSign is the
   only live provider), repoint the LARC template picker, delete
   `klara_scheduling` remnants, refresh stale docs.

## Background / verified facts

- A `SurgeryConfig` KV table + `/surgery/config` + `/surgery/admin/*`
  endpoints already exist, with a partial admin UI at `/surgery/rules`
  (4 thresholds, alert recipients, facilities, procedure templates,
  email/SMS templates).
- **Milestones are half-retired.** `_spawn_milestones()` is a no-op
  (`surgery.py:1954`) — new surgeries get NO milestone rows, so they can
  never appear in Critical Alerts / behind-schedule. Only
  Smartsheet/ModMed-imported surgeries have milestone rows
  (created in `smartsheet_seed.py:687`). The detail page already renders
  numbered **Steps** (15 hospital / 12 office, `SurgeryDetail.jsx:2947-2978`)
  computed from live surgery fields, not milestone rows.
- **BoldSign is fully independent of DocuSign**: own service
  (`boldsign_envelopes.py`), own webhook router, own env vars
  (`BOLDSIGN_API_KEY`, `BOLDSIGN_WEBHOOK_SECRET`), own DB columns
  (`boldsign_envelope_id`, `boldsign_template_id`). No fallback path sends
  via DocuSign. Legacy DocuSign envelope *rows* remain in the DB and are
  display-only.
- One LARC endpoint (`larc.py:496 /larc/docusign-templates`) still calls the
  DocuSign API to list templates for the LarcDeviceTypes admin page.

## Part 1 — Settings storage

Extend the existing `SurgeryConfig` KV table (no schema migration). Values
are JSON. A **defaults registry** in code (`surgery_settings_defaults.py`)
holds every key's default = today's hardcoded value, so a missing key always
falls back to current behavior. `_cfg()`-style reads everywhere a hardcoded
value lives today.

### Keys and defaults

**Alerts & windows** (scalars):

| Key | Default | Currently hardcoded at |
|---|---|---|
| `critical_overdue_hours` | 48 | `surgery.py:364,652` |
| `labs_alert_window_days` | 7 | `surgery.py:601` |
| `post_op_docs_alert_days` | 5 | `surgery.py:612` |
| `unresponsive_after_days` | 30 | `surgery.py:501` |
| `preop_valid_days` | 180 | `surgery.py:505` |
| `schedule_horizon_days` | 180 | `block_schedule.py:94`, `surgery.py:386` |
| `completed_window_days` | 30 | `surgery.py:331` |
| (existing) `office_full_threshold`, `office_lookahead_days`, `hospital_lookahead_days`, `reminder_lead_days` | 6 / 6 / 14 / [3,1] | already in `SurgeryConfig` |

**Step expected durations** (per step, drives behind-schedule):
`step_expected_days_hospital` and `step_expected_days_office` — JSON maps
`{step_key: days}`. Defaults derived from the closest legacy milestone
duration where a mapping exists, else a sensible default (see Part 2).

**Post-op schedules**: `post_op_schedules` — JSON list of rules, replacing
the hardcoded table in `post_op_schedule.py:49-77`:

```json
[{"match": "hysterectomy", "visits": [
    {"offset_days": 7,  "mode": "office"},
    {"offset_days": 42, "mode": "office", "location_locked": true}]},
 {"match": "myomectomy", "visits": [
    {"offset_days": 7, "mode": "office"},
    {"offset_days": 28, "mode": "telehealth"}]},
 ...]
```

Keyword match order preserved (first match wins, same as today).

**Facility capacity rules**: `capacity_rules` — JSON keyed by facility:

```json
{"medstar": {"kind": "robotic",
    "options": [{"case_kind": "robotic_180", "max": 3},
                 {"case_kind": "robotic_240", "max": 2}],
    "exclusive": true,
    "minor_addon": {"after_count": 2, "blocked_at": 3}},
 "crmc": {"kind": "mix_exclusive",
    "options": [{"case_kind": "minor", "max": 6},
                 {"case_kind": "major", "max": 2}]},
 "office": {"kind": "fixed_slots",
    "slot_times": ["07:30","08:30","09:30","10:30","11:30","14:30","15:30"],
    "case_minutes": 60}}
```

`block_schedule.py` capacity checks read from this instead of inline
constants. **Validation guardrails** on save: slot times must be distinct,
inside the block window, non-overlapping given `case_minutes`; max counts
1–20; `case_kind` must be a known procedure kind; sum of (max × duration)
warned if it exceeds the facility's block minutes.

**Procedure durations** (180/240/90/60): consolidate into the existing
procedure-templates table (`default_duration_minutes` already exists there);
`block_schedule.py`'s `DURATIONS` dict reads templates with hardcoded
fallback.

### Out of scope (stays hardcoded)

- $50K money ceiling, 25MB upload limit, 480-min max slot duration
- Status/urgency/complexity enums, facility *codes*
- CPT classification sets (revisit if classifications churn)

## Part 2 — Server-side Steps engine (full cutover)

New `backend/app/services/surgery/step_engine.py`:

- **Step catalogs** mirroring the frontend's `STEP_CFG_HOSPITAL` (15) and
  `STEP_CFG_OFFICE` (12), each step with a stable `key` (e.g.
  `surgery_info`, `benefits`, `payment`, `consents`, `select_dates`,
  `device`, `prior_auth`, `clearance`, `asst_surgeon`, `post_to_hospital`,
  `modmed_appt`, `labs`, `welfare_fu`, `notes_reports`, `bill`).
- **Completion logic** ported from the frontend's `stepCompletion()` —
  computed from live surgery fields (consents signed, payment posted,
  dates picked, labs_sent_to_hospital, billed_at, …). The frontend's
  versions in `SurgeryDetail.jsx` are replaced by consuming the API's
  per-step state (single source of truth; the serializer adds a
  `steps` array: `{key, n, title, state, optional, applicable}`).
- **Current step** = first non-done applicable step.
- **Behind-schedule** = `now - current_step.entered_at >
  expected_days(step) + grace`. `entered_at` approximated as
  `max(completed_at of prior steps, status-transition timestamp)`; where no
  timestamp exists, surgery `created_at`. Grace = existing 2-day rule,
  folded into `critical_overdue_hours`.
- **Dashboard cutover**: `_current_milestone` / `_behind_schedule` /
  stuck-count / Critical Alerts / calendar readiness flags all switch to the
  step engine. Works for imported AND new surgeries.
- **Milestone retirement**: all milestone read paths removed
  (`_current_milestone`, milestone auto-advance hooks, milestone
  serialization, `smartsheet_seed` milestone creation). The
  `surgery_milestones` table is kept as dormant audit history — no reads,
  no writes, no drop.
- Existing milestone-kind admin endpoints (`skip-retired-milestones`)
  retained but inert.

Terminology: all UI copy says **Steps** ("Step 7 of 15", "stuck on step",
"expected days per step"). No DB renames.

## Part 3 — Settings UI

New page `frontend/src/pages/SurgerySettings.jsx`, route
`/surgery/settings` (M.SURGERY, TIER.MANAGE). Gear/Settings button in the
Surgery dashboard header. Title Case headers, MM/DD/YYYY dates.

Tabs:

1. **Alerts & Windows** — numeric fields for every scalar key above +
   reminder lead days + alert recipients (moved from Rules).
2. **Workflow Steps** — two lists (Hospital / Office) of numbered steps,
   editable expected-days per step; step titles editable (stored in config,
   default from catalog); order fixed.
3. **Post-Op Schedules** — editable rule rows (procedure keyword, visit
   offsets/modes), add/remove visits and rules; preview of which rule a
   sample procedure name matches.
4. **Facilities & Capacity** — facility CRUD (moved from Rules) + per-
   facility capacity editor (case-kind maxes, exclusivity, office slot
   times) with the validation guardrails server-enforced and surfaced
   inline.
5. **Templates** — email + SMS + procedure templates (moved from Rules).

`/surgery/rules` becomes a redirect to `/surgery/settings`; its
explanatory/documentation prose moves to a collapsible "How This Works"
panel inside Settings, rewritten to drop Klara-automation and DocuSign
references.

API: extend `GET/PUT /surgery/config` to cover the new keys with per-key
server-side validation (types, ranges, JSON schemas for the structured
configs). Invalid saves → 422 with field-level messages.

## Part 4 — Dead-code cleanup

**Pre-flight (hard gate):** count DocuSign envelopes still out for
signature (`docusign_envelope_id IS NOT NULL AND status NOT IN
('signed','completed','declined','voided')`) via the deployed backend
(Cloud SQL is private-IP-only). If any are open, pause removal of the
webhook until they finalize or are voided; everything else proceeds.

Remove:
- `backend/app/routers/docusign.py` (webhook) — gated on pre-flight
- `backend/app/services/docusign_envelopes.py`, `docusign_client.py`
- `docusign-send` / `docusign-sync` endpoints (`surgery.py:4422,4461`) and
  their frontend mutations/UI branches in `SurgeryDetail.jsx` (provider
  display for legacy envelopes stays — rows still render as "docusign")
- `docusign_*` settings in `config.py` + template seeding in `database.py`
- `backend/tests/test_docusign_email.py`
- `klara_scheduling` remnants: `SurgeryDetail.jsx:3540` case,
  `smartsheet_seed.py` status-mapping references (milestone creation goes
  away entirely with Part 2)

Repoint:
- `/larc/docusign-templates` → `/larc/boldsign-templates` listing BoldSign
  templates; `LarcDeviceTypes.jsx` updated accordingly.

Keep:
- DB columns `docusign_envelope_id`, `docusign_template_id` (historical
  rows, display)
- `klara_drafter.py` + klara-draft endpoints (active manual-paste workflow)
- Legacy-path 410 guards (`is_legacy_local_path`) — cheap defense
- DocuSign Secret Manager secrets (defense: deleted only after one clean
  deploy cycle)

## Error handling

- Config reads never fail the request: bad/missing JSON → fall back to code
  default and log a warning.
- Config writes validated server-side; structured configs validated against
  JSON schemas; capacity guardrails enforced on save, not just in UI.
- Step engine pure-functional over the surgery row — no new writes; safe to
  compute in list serializers (it replaces equivalent milestone queries; the
  per-surgery milestone query goes away, net DB load decreases).

## Testing

- Unit: step catalogs + completion logic (fixtures for hospital/office
  surgeries at each stage), behind-schedule math, config fallback behavior,
  capacity-rule validation (accept/reject cases), post-op schedule matcher
  parity with the old hardcoded table.
- Parity check: for a sample of imported surgeries, old milestone-based
  "stuck" verdicts vs new step-based verdicts — differences reviewed, not
  assumed wrong (the catalogs differ by design).
- API: PUT /surgery/config rejects out-of-range values; GET returns merged
  defaults.
- Frontend: settings tabs render, save, and surface 422 validation; Surgery
  dashboard gear navigates; SurgeryDetail timeline renders from API steps.
- Manual smoke on Cloud Run after deploy: dashboard Critical Alerts shows
  step-based entries for both an imported and a freshly created surgery.

## Rollout

1. Backend: settings keys + step engine + cutover behind the same deploy
   (full cutover per decision), DocuSign removal in the same release except
   the webhook (pre-flight gated).
2. Frontend: settings page + steps-from-API + gear button.
3. Deploy via `gcloud builds submit --tag=...` (never --config), then
   `gcloud run deploy`. `STORAGE_BACKEND=gcs` unchanged.
4. Post-deploy: run pre-flight envelope count; if zero, remove webhook +
   DocuSign secrets in a follow-up commit.

## Decisions log

- Scope: all four config groups (alerts/windows, step durations, post-op
  schedules, capacity & slot times) — Oliver, 2026-06-12
- New separate Settings page (not expanding Rules) — Oliver
- BoldSign only; DocuSign removed after verification — Oliver
- Access: Surgery MANAGE tier — Oliver
- Milestone → Steps: full cutover, milestone table kept dormant — Oliver
