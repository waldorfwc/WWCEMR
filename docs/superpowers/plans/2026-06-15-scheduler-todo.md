# Scheduler To-Do + Activity — actionable, tied to the steps engine

> subagent-driven-development. Backend TDD; suite baseline 69 failed / 0 errors. Frontend build + headless load before deploy.

**Branch:** `feat/scheduler-todo` off `main`.

**Goal:** One scheduler workspace with two surfaces:
1. **Action Needed (to-do)** — a live, cross-surgery list of the next open step per active surgery, derived from the **steps engine** (so it auto-resolves the moment a step completes). Overdue/behind items float to the top → these ARE the "missed-action alerts".
2. **Recent Activity (notifications)** — a persisted feed of each patient action (slot claimed, consent signed/declined, doc uploaded, labs self-reported, paid, date-change requested) + system events (auto-unresponsive), with unread/read state and a nav badge → this is "the scheduler is notified of each action the patient takes."

## Design decisions
- **To-dos are NOT persisted** — computed live from `step_engine.compute_steps` + `is_behind`. This is what "ties into the steps engine" + "auto-resolves when the step completes" means. No drift, no manual check-off; completing the step (via existing UI) clears the item.
- **Activity IS persisted** (point-in-time events with read state) in a new `SurgeryActivity` table.
- Tier: coordinators = `Module.SURGERY, Tier.WORK` for the page, feed, and mark-read.

## Current state (verified)
- Steps: `backend/app/services/surgery/step_engine.py` `compute_steps(s, titles=None)`, `current_step(s)`, `is_behind(s, grace_hours)`, expected-date helpers. Dashboard `_surgery_buckets` (routers/surgery.py ~527) already derives needs_* from steps.
- Patient actions: `notify_scheduler()` already fires email on date_picked/rescheduled/cancelled (patient_surgery.py ~470/545/710) and consent signed/declined (boldsign.py ~204). SILENT today: document upload (patient_portal.py ~1172/1216), labs self-report, payment, date-change request.
- Notification infra: `scheduler_notify.py`, `checklist_notifications.send_email/slack`. `SurgerySchedulerNotice` is an email idempotency ledger (not a feed).
- Nav badge precedent: the Messages badge in `frontend/src/components/surgery/SurgeryNav.jsx` (polls `/staff/messages/inbox`).
- `_apply_lightweight_migrations()` in database.py for column/— new table auto-creates via Base metadata; register the model import.

---

## B1 — SurgeryActivity model + helper
**Files:** create `backend/app/models/surgery_activity.py`; register import in `backend/app/database.py` model-import line (so create_all builds it); test.
```python
class SurgeryActivity(Base):
    __tablename__ = "surgery_activity"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id = Column(GUID(), ForeignKey("surgeries.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(40), nullable=False)     # date_picked | rescheduled | cancelled | consent_signed | consent_declined | document_uploaded | labs_reported | payment_made | date_change_requested | auto_unresponsive | step_overdue
    summary = Column(String(300), nullable=False) # human one-liner
    actor = Column(String(20), nullable=False, default="patient")  # patient | system
    created_at = Column(DateTime, default=now_utc_naive, nullable=False, index=True)
    read_at = Column(DateTime, nullable=True)
    read_by = Column(String(200), nullable=True)
```
Add `record_activity(db, surgery, kind, summary, actor="patient")` in a small `backend/app/services/surgery/activity.py` — inserts a row, soft-fail (never break the patient action), commit-safe (caller commits, or flush). Mirror the soft-fail style of scheduler_notify.
Test `backend/tests/test_surgery_activity.py`: record_activity inserts; the table is excluded from the soft-deleted-surgery filter only via its own surgery (N/A — activity has no soft-delete). Commit `feat(surgery): SurgeryActivity model + record_activity helper (B1)`.

---

## B2 — Wire activity hooks on patient actions
**Files:** `patient_surgery.py`, `patient_portal.py`, `boldsign.py`, `stripe_payments.py`, `auto_unresponsive.py`, `escalations.py`.
At each site, after the action succeeds, call `record_activity(...)`:
- date_picked (patient_surgery patient_pick) → "Patient picked a date: {date} at {facility}"
- rescheduled → "Patient rescheduled to {date}"
- cancelled → "Patient cancelled ({reason})"
- consent_signed / consent_declined (boldsign webhook) → "Consent {signed|declined} ({template})"
- document_uploaded (patient_portal clearance/FMLA/other upload) → "Uploaded {kind} document"
- labs_reported (patient self-report labs) → "Self-reported labs {result/date}"
- payment_made (stripe success path) → "Paid ${amount}"
- date_change_requested (patient_surgery request_date_change) → "Requested a date change"
- auto_unresponsive (auto_unresponsive.py, actor=system) → "Auto-marked unresponsive (no activity {N}d)"
- step_overdue (escalations.py, actor=system, dedup with the existing escalation_state so it logs once per overdue step) → "Overdue: {step title} ({days}d behind)"
Keep the existing notify_scheduler email calls (don't remove). Where notify_scheduler already fires, add record_activity alongside. Don't double-log.
Add a parity test in test_surgery_activity.py: hitting the patient date-change endpoint (or simulating the call) creates a date_change_requested row. Suite ≤ baseline. Commit `feat(surgery): log patient + system actions to the activity feed (B2)`.

---

## B3 — To-Do endpoint (live, step-derived)
**File:** `backend/app/routers/surgery.py`, test.
`GET /surgery/todos` (Tier.WORK), optional params `behind_only: bool=False`, `limit:int=200`. For each active surgery (status in new/in_progress/confirmed; include incomplete? NO — incomplete has no steps), compute the CURRENT open step via the steps engine and whether it's behind:
```json
{ "items": [ {
    "surgery_id","patient_name","chart_number","surgery_number",
    "step_key","step_title","state",          // "behind" | "open"
    "expected_date","days_behind",            // days_behind>0 when behind
    "scheduled_date","facility"
} ], "behind_count": N, "open_count": M }
```
Sort: behind first (most days_behind first), then open by expected_date asc/nullslast. Reuse `compute_steps`/`is_behind`/`titles_map` exactly as `_surgery_buckets`/`_surgery_dict` do (grep their usage). Exclude soft-deleted (global filter already handles). Test: a surgery with an overdue step appears with state "behind" + days_behind>0; an on-track one appears "open"; behind_only filters. Commit `feat(surgery): /surgery/todos live action list from steps engine (B3)`.

---

## B4 — Activity feed endpoints
**File:** `backend/app/routers/surgery.py` (or a small `surgery_activity` router), test.
- `GET /surgery/activity` (Tier.WORK) params `unread_only:bool=False`, `limit:int=100` → newest-first list joined to surgery (id, surgery_id, patient_name, chart_number, kind, summary, actor, created_at, read_at). Exclude activity whose surgery is soft-deleted (join; global filter on Surgery covers it if joined via relationship/subquery — verify, else filter explicitly).
- `GET /surgery/activity/unread-count` (Tier.WORK) → `{count}` (for the nav badge).
- `POST /surgery/activity/{id}/read` (Tier.WORK) → stamp read_at/read_by.
- `POST /surgery/activity/read-all` (Tier.WORK) → mark all unread read.
Tests: seed activity, list returns newest-first, unread-count correct, mark-read + read-all flip state. Commit `feat(surgery): activity feed endpoints (list/unread-count/read) (B4)`.

---

## F1 — Scheduler To-Do page + nav tab
**Files:** create `frontend/src/pages/SurgeryTodo.jsx`; `routes.jsx` (child of /surgery layout, Tier.WORK, path "todo"); `SurgeryNav.jsx` (add "To-Do" item, WORK).
Page = two panels:
- **Action Needed** (from `/surgery/todos`): a table/list of items; behind rows flagged red with "{n}d behind"; each row links to `/surgery/{surgery_id}`. A "Behind only" toggle. Show counts ("12 open · 3 behind").
- **Recent Activity** (from `/surgery/activity`): newest-first feed; unread rows highlighted with a dot; each links to the surgery; a "Mark all read" button; clicking a row marks it read (`POST .../{id}/read`) and navigates. Poll/refetch on an interval.
Build clean. Commit `feat(surgery-todo): Scheduler To-Do page (Action Needed + Recent Activity) + nav tab (F1)`.

---

## F2 — Activity badge in the surgery nav
**File:** `frontend/src/components/surgery/SurgeryNav.jsx`.
Add an unread-activity badge on the "To-Do" nav item (mirror the existing MessagesBadge: `useQuery(['surgery-activity-unread'], () => api.get('/surgery/activity/unread-count'), {refetchInterval:60000})`; red pill when >0). Keep render-time navItems() (no top-level TIER/MODULE). Build clean. Commit `feat(surgery-todo): unread-activity badge on the To-Do nav item (F2)`.

---

## F3 — Headless smoke + deploy
1. build + vite preview + Playwright load `/surgery/todo` + `/surgery` → /login, 0 console errors.
2. Merge to main; deploy backend then frontend; smoke (`/api/surgery/todos` 401, `/api/surgery/activity` 401, `/surgery` 200, health 200); push origin.
3. Authed check: Action Needed lists open/behind steps and auto-clears when a step is completed; patient actions appear in Recent Activity with the badge; mark-read works.

## Out of scope (v1)
- No per-coordinator assignment/filtering (everyone sees the same queue).
- No email/Slack changes (existing escalation emails stay; this is the in-app surface).
- No manual check-off of step to-dos (they resolve via the steps engine by design).
