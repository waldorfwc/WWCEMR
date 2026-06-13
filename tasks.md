# Implementation Plan — Session Resumption Notes

Updated 2026-06-13. Pick up here next time.

---

## 🟣 Surgery Settings + Steps Engine + DocuSign Removal (branch `feat/surgery-settings`)

Status: **DEPLOYED to Cloud Run 2026-06-13** from `feat/surgery-settings`
(NOT yet merged to `main`; PR pending). Live revisions:
**backend-00303-qwj**, **frontend-00233-wf5**. Automated smoke passed
(health 200; all new settings/step/capacity/boldsign endpoints 401-gated =
live; removed docusign webhook + larc/docusign-templates → 404). UI smoke
(below) still needs a human pass. 21+ commits on `feat/surgery-settings`
(off `main`). Backend imports clean, frontend builds clean, test suite at
its pre-existing baseline (87 failed / 7 errors — all pre-existing
missing-module/collection failures unrelated to this work; was 90 before,
−3 from deleting test_docusign_email.py). Spec: `docs/superpowers/specs/2026-06-12-surgery-settings-design.md`.
Plan: `docs/superpowers/plans/2026-06-12-surgery-settings.md`.

### What shipped (all committed)
- **Surgery Settings page** at `/surgery/settings` (gear button on dashboard,
  MANAGE tier). 5 tabs: Alerts & Windows, Workflow Steps, Post-Op Schedules,
  Facilities & Capacity, Templates. `/surgery/rules` now redirects there;
  `SurgeryRules.jsx` deleted.
- **Everything config-driven** via `SurgeryConfig` + `app/services/surgery/settings.py`
  registry (defaults = old hardcoded values, so behavior is identical until
  edited): alert thresholds/windows, milestone→step expected-days, post-op
  schedules, facility capacity rules + office slot times. All PUT-validated.
- **Milestone → Steps cutover (full):** new `step_engine.py` is the single
  source of truth; dashboard Critical Alerts / behind-schedule / readiness /
  buckets all run on steps. **Fixes the bug where newly-created surgeries were
  invisible to alerts** (milestone spawn had been a no-op). Milestone writes
  retired; `surgery_milestones` table kept as dormant history. Frontend
  consumes `surgery.steps`.
- **DocuSign fully removed** (BoldSign-only): router/webhook, services, client,
  send/sync endpoints, config, tests deleted; LARC template picker repointed to
  `/larc/boldsign-templates`; legacy `docusign_envelope_id`/`docusign_template_id`
  columns retained for read-only historical display. Pre-flight endpoint
  `GET /api/admin/cleanup/docusign-open-count` added.
- **klara_scheduling** comment remnants cleaned (Klara stays manual-paste).

### Before you DEPLOY (decisions/notes)
1. **Review/merge first** — user is holding deploy to review the branch. Merge
   to `main` (or deploy the branch) when ready.
2. **Witness env (corrected):** consent-envelope witness reads
   `CONSENT_WITNESS_EMAIL`/`CONSENT_WITNESS_NAME` (provider-neutral), falling
   back to `DOCUSIGN_WITNESS_*`. NEITHER is set on the backend Cloud Run
   service today, so consent envelopes currently have **no witness** — the
   DocuSign removal does NOT change this. If a witness IS wanted, set
   `CONSENT_WITNESS_NAME` + `CONSENT_WITNESS_EMAIL` (need the values) at deploy.
   No DOCUSIGN_* secret dependency for witness after all.
3. **Secret Manager docusign-* secrets:** can be deleted in a later cleanup;
   no code references them after this branch (only the moot witness fallback).

### Deploy runbook (T18 — not yet run)
```
# confirm current image paths first:
gcloud run services describe backend  --region=us-east4 --project=wwc-solutions \
  --format="value(spec.template.spec.containers[0].image)"
# backend:
cd backend
gcloud builds submit --tag=us-east4-docker.pkg.dev/wwc-solutions/<repo>/backend:latest --project=wwc-solutions
gcloud run deploy backend  --image=us-east4-docker.pkg.dev/wwc-solutions/<repo>/backend:latest \
  --region=us-east4 --project=wwc-solutions   # keep STORAGE_BACKEND=gcs
# frontend: same pattern for the frontend service.
```
Post-deploy smoke (8 pts): dashboard Critical Alerts populated (step-based);
a freshly-created surgery is now alertable; SurgeryDetail timeline renders
from API steps (office=12, hospital=15); /surgery/settings 5 tabs load;
change Critical Overdue 48→72 shrinks red set; MedStar 4th 180-min case still
rejected; BoldSign consent send works + legacy DocuSign envelope read-only;
LARC device-type page lists BoldSign templates. Then update the live-revision
note below.

### Follow-ups (non-blocking)
- UI control to record post-op call (welfare_fu step reads
  `post_op_call_status`; old milestone card that set it is retired — staff
  currently have no control to mark "Spoke to Pt."). See task list.
- Delete docusign-* Secret Manager secrets after a clean deploy cycle.

---

## START HERE WHEN YOU COME BACK

Open this list, work through it top-down. Everything else further down is
context / reference.

### 1. Decisions only you can make

1. **Penn, Brandy (chart 15794)** — schedule her surgery.
   - Surgery ID: `598507d1-f8e9-4b4a-b0b0-3cf1067cd2d2`
   - Status: `incomplete`, no slot held.
   - Spreadsheet date 06/22 13:30 is taken by Blackstone now. Her picker's
     next-available is **07/01 13:30 MedStar**.
   - 3 choices:
     - **A.** Open her chart → click `pick` → choose 07/01 13:30 or later
       (fires patient email + SMS — standard flow).
     - **B.** Move Blackstone off 06/22 13:30 first, then book Penn there.
     - **C.** Tell me "silent-schedule Penn 06/22 13:30" and I'll force-book
       her without firing comms (would still conflict with Blackstone — pick
       a different date if going silent).
2. **Test, Oliver (chart 99999)** — leftover sentinel test patient.
   - Surgery ID: `1ee02efe-2759-4a9a-b512-bebf054a513a`
   - Status: `cancelled`, invisible to all active views.
   - Decide: leave as-is (audit history preserved) OR tell me to
     hard-delete the row + cascade-delete his milestones.
3. **Two Dana Clarks** in the chart system.
   - Chart 10260 AND chart 19623, both named "Dana Clark".
   - Decide: same patient duplicated → merge; OR two real patients with the
     same name → leave separate.
4. **Linkins, Patryce confirmation** — when I booked her on 06/17 07:30 I
   used the standard `/schedule` endpoint which **fires patient email +
   SMS**. If you'd already contacted her separately, she may have gotten a
   duplicate confirmation. Verify on her phone / Klara before her surgery if
   she pings staff confused.

### 2. CRMC-Minor patients you haven't manually scheduled yet

36 patients were unstuck this session (added `crmc-minor` to the
appointment-type map + ran the backfill endpoint). They now have valid
`procedure_classification` + `eligible_facilities` so their pickers work,
but they're still `incomplete` with no scheduled_date. They'll appear
under the surgery dashboard's incomplete bucket.

Sample (full list is anyone with `procedure label = "CRMC-Minor"` and no
scheduled_date):
- Gross, Tanya (26163)
- Medlin, Tamara (48569)
- Feldman, Stacy (17876)
- Lindsey, Juqweis (16780)
- Benoit, Sheila (30151)
- Santiago Quinones, Amy (MM0000000704)
- Davis, Angela (37212)
- Burdine, Betsy (14543)
- Hudson, Cheryl (19557)
- ...plus 27 more.

Each one is a standard manual schedule (open the chart, click `pick`).

### 3. Real prior-auth backlog (Critical Alerts panel)

The Critical Alerts card on the Surgery dashboard now surfaces REAL
backlog instead of the phantom Klara list. 10 surgeries stuck at "Prior
auth received" milestone, ranging 5–6d late. Top of the list:
- Danielle Armstrong (6d)
- Latrisha Perkins (6d)
- Atinuke Arigbabu (6d)
- Keonna Lockerman (6d)
- Ayesha Rucker (6d)
- Celica Innis Richardson (6d)
- Natalie Branson (6d)
- Casey McDonagh — Assistant surgeon coordinated (5d)
- Marielle Robinson (5d)
- Menyon Keys — Post-op appointments scheduled (4d)

These were already there before this session, the dashboard just couldn't
surface them through the Klara noise.

---

## Live revisions

- Backend: **`backend-00302-tb8`** (CRMC-Minor mapping + everything below)
- Frontend: **`frontend-00232-mn6`** (override-reason capture + everything
  below)

If you redeploy without these revisions or hit weird behavior, those are
the "known-good" baseline.

---

## Completed this session

### Surgery — booking + scheduling bugs

- `book_slot` enforces blackouts under the row lock (defense-in-depth).
- `procedure_kind` namespace bug fixed (`bd.block_kind` → `s.procedure_
  classification`). Killed the "MedStar block doesn't accept robotic_only
  cases" rejection.
- Date-picker now finds gaps before / between / after existing slots, not
  only post-last-slot. Linkins regained 06/17 07:30.
- Partial-day blackouts respected: callers pass start/end window;
  `materialize_block_days` only skips BlockDay creation for whole-day
  blackouts.
- Override-reason flow: picker captures a reason on >10% duration mismatch
  instead of dead-ending on a raw 422.
- `patient_picks_date` milestone auto-advances after `book_slot` success.

### Surgery — data integrity

- **77 orphan `SurgerySlot` rows deleted** (cause of the 06/22 capacity
  ghost).
- FK changed to ON DELETE CASCADE on `surgery_slots.surgery_id`.
- **77 orphan `klara_scheduling` milestones** marked skipped + read-path
  `_current_milestone()` filters retired catalog kinds. Critical Alerts
  cleared from phantom Klara to real prior-auth backlog; stuck count
  50 → 11.

### Surgery — ModMed bulk import

- Importer reads Appointment Type / Date / Time.
- `APPT_TYPE_MAP` covers MedStar-Robot-Short/Long, MedStar-Minor,
  **CRMC-Minor**, CRMC-Major, Office-Based Surgery.
- Importer stamps `procedure_classification`, `eligible_facilities`,
  `is_robotic`.
- `backfill_mode=true` flag bypasses capacity / overlap / block-window /
  blackout guards. Refuses same-start-time conflicts.
- 18 import patients confirmed on spreadsheet dates.
- Backfill endpoint walked existing `candidate_imported` rows and fixed
  36 stuck CRMC-Minor patients.

### Surgery — name editor

- Inline edit on SurgeryDetail header (First / MI / Last).
- `SurgeryPatch` accepts the three structured fields.
- `patient_name` auto-rebuilds as "Last, First".
- Used to fix chart 45473 Jenny → "Rodriguez-Gonzales, Jenny".

### Dashboard / next-available / under-booked alerts

- Office under-booked + scheduler-alerts + next-available all skip
  blackouts.
- Hospital release alerts skip blackouts.
- Surgery search input no longer wipes the page mid-type
  (`keepPreviousData`).
- Underline-tab active state harmonized.

### Date / time hygiene

- `fmt.weekday()` helper (timezone-safe parser). Fixed Thanksgiving /
  Christmas / NYE weekday-off-by-one.
- Raw `YYYY-MM-DD` displays swapped to MM/DD/YYYY per project convention.

### Fax pipeline

- RC auth via env-backed Secret Manager.
- `pdf_merge` + `send_fax` go through `storage.read_blob`.
- Legacy `/Volumes/OWC External/...` → GCS keys at fetch time.
- `RC_SERVER_URL` `.strip()` (whitespace was busting httpx IDNA).
- Faxes scope added to the WWC Recall RC app + JWT regenerated.
- End-to-end fax verified, RC returned message_id.

### Test-patient cleanup

- 31 LARC test rows soft-deleted.
- Surgery + Pellet scan added (read-only). 1 leftover (Test Oliver 99999).
- Admin endpoints retained for future use:
  `admin/cleanup/test-patients`, `silent-schedule`, `unbook-surgeries`,
  `delete-orphan-slots`, `skip-retired-milestones`,
  `backfill-imported-procedure-classification`,
  `fix-imported-confirmed-status`.

### Backend bugs

- `/api/personal-tasks` 500 (JSON LIKE on Postgres) — cast to String.
- `/api/surgery/scheduler-alerts` 404 → 500 → working (route order + missing
  constant import).
- Personal-tasks assignee/shared_with dedup.
- SoftDeleteMixin read-path filters on LARC list + dashboard.

### UI consistency (earlier in the session)

- Palette ghost shades defined.
- `stone-*` → `plum-*`. `text-primary-*` → `text-plum-*`.
- 9px → 11px font floor (86); 10px uppercase → 11px (507).
- `.chip-*`, `.page-title`, `.display-number` brand classes.
- `EmptyState` + `LoadingState` components wired across 34 spots.
- `border-gray-{100,200}` → `border-border-subtle` (181).
- SVG favicon + `theme-color` meta.
- `[object Object]` in procedure-list render — fixed with `p.name`
  fallback.

---

## Cheat-sheet: admin endpoints

| Endpoint | What it does |
|---|---|
| `GET  /api/admin/cleanup/test-patients` | List test-pattern matches across LARC + Surgery + Pellet |
| `DELETE /api/admin/cleanup/test-patients?confirm=true` | Soft-delete LARC test rows |
| `POST /api/admin/cleanup/delete-orphan-slots` | Drop SurgerySlot rows with NULL surgery_id |
| `POST /api/admin/cleanup/skip-retired-milestones` | Skip milestones whose kind is no longer in the catalog |
| `POST /api/admin/cleanup/backfill-imported-procedure-classification` | Walk candidate_imported surgeries, stamp procedure_classification + eligible_facilities |
| `POST /api/admin/cleanup/fix-imported-confirmed-status` | Bump scheduled imported surgeries from incomplete → confirmed |
| `POST /api/admin/cleanup/silent-schedule` | Book a surgery without firing patient email/SMS/calendar |
| `POST /api/admin/cleanup/unbook-surgeries` | Drop a surgery's slot, reset to incomplete |
| `POST /api/surgery/candidates/bulk-import?backfill_mode=true&dry_run=false` | ModMed roster import in backfill mode |

All super-admin gated.

---

## Optional follow-ups (not blocking anything)

- Sweep raw `text-gray-500` body text → `text-muted` (989 callsites — risky
  warm-shift, deferred).
- Workflow-state tone palette consolidation (LarcDevices / ActiveAR have
  violet / teal / indigo one-offs). Categorical distinction is real, so
  treat carefully.
- Modal/dialog standardization (large refactor; benefits not quantified).
- Audit other historical ModMed appointment-type labels. Only CRMC-Minor
  surfaced today; if more appear, add to `APPT_TYPE_MAP` and re-run the
  backfill endpoint.
