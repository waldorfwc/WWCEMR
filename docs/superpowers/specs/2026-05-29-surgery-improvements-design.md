# Surgery module improvements — design spec

**Status:** draft, awaiting user review
**Date:** 2026-05-29
**Author:** ocooke@waldorfwomenscare.com (drafted by Claude)
**Scope:** Six changes to the surgery module. Single spec; phased plan.

---

## Background

The surgery module today has:

- `Surgery`, `SurgeryMilestone`, `BlockSchedule`, `BlockDay`, `SurgerySlot`, `SurgeryBlackoutDay`, `SurgeryWaitlist`, `SurgeryNotification`, `SurgeryNote` models.
- `surgery_release_alerts.py` running a daily cron that emails hospital release alerts (empty block days 14d out) and office release alerts (office days exactly 6d out with <6 procedures).
- A `WeeklyCalendar` component at `/surgery/calendar` that is also embedded compact on `/surgery`.
- A patient-facing scheduling page at `/p/surgery/:id` (mobile-first, no app auth) where the patient picks an available block day.
- A coordinator-facing detail page `/surgery/:id`.
- A `SurgeryRules` admin page (`/surgery/rules`) used for milestone rules.

This spec adds six independent capabilities. They share enough infrastructure (config + admin UI) that they go in one spec, but are delivered in four phases.

## Goals

1. Give the scheduler a monthly calendar view in addition to weekly.
2. Surface scheduling conflicts when blackouts are added on top of booked surgeries.
3. Make the existing low-office-volume alert recipient + threshold configurable.
4. Surface the columns the waitlist actually needs in the UI.
5. Replace hardcoded thresholds, recipient lists, facility identifiers, and procedure durations with admin-managed configuration.
6. Polish patient self-scheduling (highlight earliest slot) and give the coordinator override (pick slot for patient, adjust duration).

## Non-goals

- Building a new notifications/email infrastructure. We use the existing `checklist_notifications` service.
- Building a new public-patient auth flow. We reuse the existing token-gated patient page.
- Migrating away from the `procedure_kind` enum. We add procedure templates on top of the enum as a richer label/duration layer.
- Decommissioning the existing role-based recipient query in `release_alerts.py`. It becomes the fallback if the configurable list is empty.

---

## Section 1 — Monthly calendar (`/surgery/calendar`)

### Goal

Show a calendar-grid view of an entire month at `/surgery/calendar`. Leave `WeeklyCalendar` unchanged everywhere else (still embedded on `/surgery`).

### UI

- New component `MonthlyCalendar` in `frontend/src/pages/SurgeryCalendar.jsx` (same file, exported alongside `WeeklyCalendar`).
- Layout: 6-row × 7-col grid (Mon–Sun columns; rows are 7-day weeks, including overflow into prev/next month, dimmed).
- Each day cell shows:
  - The date number (top-left).
  - A small `facility chip` per surgery (`MedStar`/`CRMC`/`Office`), with one indicator dot per surgery using the existing `INDICATOR_TONE` (green/yellow/red).
  - If more than ~6 fit in a cell, show "+N more" link.
  - Clicking a chip routes to `/surgery/:id`. Clicking the date number opens the weekly view anchored to that week.
- Toolbar: prev / next / today buttons; "Week ↔ Month" toggle. The page reads the toggle from a URL param (`?view=month|week`).
- Default view at `/surgery/calendar` is **month**.
- Embedded version on `/surgery` keeps `<WeeklyCalendar compact />` as today.

### Backend

The existing `GET /api/surgery/calendar?start=&end=` endpoint already supports arbitrary date ranges. No backend change.

### Tests

- `tests/test_surgery_calendar_monthly.py` — endpoint returns expected shape for a month range.
- Frontend: smoke test via Playwright that month view renders 28–31 days plus padding and that clicking a day surgery navigates correctly.

---

## Section 2 — Blackout-date conflict alert

### Goal

When a `SurgeryBlackoutDay` overlaps an existing booked `Surgery.scheduled_date`, the scheduler should see a To-do on the surgery dashboard telling them to "release the date with the hospital" or move the surgery.

### Backend

- New service `backend/app/services/surgery_blackout_conflict.py` with a single function:

  ```python
  def find_blocked_conflicts(db: Session) -> list[dict]:
      """For each Surgery whose scheduled_date matches a SurgeryBlackoutDay
      with the appropriate scope, return a dict with the surgery + the
      blackout reason."""
  ```

  Conflict detection logic per `SurgeryBlackoutDay.scope`:
  - `office`: matches any surgery on that date (whole-practice closure).
  - `provider`: matches any surgery on that date. The practice currently has a single operating surgeon (Aryian Cooke, MD), so provider PTO effectively grounds the whole day. If the practice ever onboards a second operating surgeon, we add a `Surgery.surgeon_email` field and tighten this match — tracked as a known limitation, not blocking.
  - `facility`: matches surgeries whose `selected_facility == facility` on that date.

- New nullable column `Surgery.blocked_conflict_notified_at` (`DateTime`). Set when the user clicks "Mark hospital notified" so the conflict drops off the dashboard.

- Existing endpoint `GET /api/surgery/dashboard` returns a new list under key `blocked_conflicts`. Each entry: `{surgery_id, patient_name, scheduled_date, facility, blackout_reason, blackout_label}`.

- New endpoint `POST /api/surgery/{surgery_id}/blocked-conflict/resolve` (perm `claim:edit`) — sets `blocked_conflict_notified_at = now()`, writes a `SurgeryNote` of kind `blocked_conflict_resolved`.

### Frontend

- `ToDoPanel` (already in `Surgery.jsx`) gains a third section "Blocked-day conflicts (N)" between Critical alerts and Hospital release / Office release sections.
- Each row: patient name, date, facility, the blackout reason as a tag, and a **Mark hospital notified** button. Clicking calls the resolve endpoint and removes the row from the list.

### Tests

- `tests/test_surgery_blackout_conflict.py` — covers all three scopes, the resolve flow, and idempotency.

---

## Section 3 — Low-office-volume alert: configurable

### Goal

The existing rule already fires for any office day 6 days out with <6 procedures. Make the threshold, lookahead, and recipient lists admin-editable. Keep the behavior identical otherwise.

### Backend

- New table `surgery_config(key VARCHAR PK, value JSON)`. Used as a simple key-value config store for the surgery module. Keys we use immediately:
  - `office_full_threshold` (int, default `6`)
  - `office_lookahead_days` (int, default `6`)
  - `hospital_lookahead_days` (int, default `14`)
- New table `surgery_alert_recipients(id, alert_kind VARCHAR, email VARCHAR, added_by, added_at)`. `alert_kind` is one of `office_release` | `hospital_release`. Unique on `(alert_kind, lower(email))`.
- `surgery_release_alerts.py` reads:
  - thresholds from `SurgeryConfig` (with the existing constants as defaults if a row is missing).
  - recipients from `SurgeryAlertRecipient` (falls back to the current role-based User query when the list is empty for that kind, so we never silently lose alerts during rollout).
- The cron schedule does not change.

### Frontend

Handled by Section 5 (the admin UI lives on the SurgeryRules page).

### Tests

- `tests/test_surgery_release_alerts.py` extended: covers config-driven thresholds, configured recipient list, empty-list fallback to role-based query.

---

## Section 4 — Waitlist columns

### Goal

`/surgery/waitlist` table should show: patient, advance notice days, surgery type, location, urgency. Sortable by urgency / notice / facility.

### Backend

- New column `Surgery.urgency` (`String(20)`, nullable, values `routine` | `expedited` | `urgent`; default `routine`). Migration backfills `routine` for existing rows.
- `GET /api/surgery/admin/waitlist` adds these fields to each waitlist row:
  - `patient_name`
  - `advance_notice_days` (already present)
  - `procedure_name` — taken from `Surgery.procedures[0].name` (the primary procedure) or `null` if `procedures` is empty
  - `facility` — the booked `Surgery.selected_facility` if set, otherwise the first entry of `Surgery.eligible_facilities` (a JSON list of facilities the patient is eligible for). The existing endpoint already returns `eligible_facilities`; we add a new `facility` key with the resolved value so the UI can render one chip.
  - `urgency` — new field

### Frontend

- `SurgeryWaitlist.jsx` table gains columns:
  - **Notice** — number of days
  - **Type** — procedure name
  - **Location** — facility label (using the new facilities config from Section 5)
  - **Urgency** — pill with tone (routine grey, expedited amber, urgent red)
- Sortable column headers (notice asc/desc, urgency by enum rank, facility alpha).
- Coordinator can edit `urgency` from the `SurgeryDetail` page via the existing patch endpoint.

### Tests

- `tests/test_surgery_waitlist_columns.py` — endpoint returns the new fields; sort by urgency works.

---

## Section 5 — Surgery configuration admin

### Goal

Replace hardcoded thresholds, recipient lists, facility labels, and procedure durations with admin-editable config. The admin UI lives on `/surgery/rules` (the existing `SurgeryRules` page) as new tabs.

### Backend

Tables introduced (each gets a `created_by`, `created_at`, `updated_by`, `updated_at` audit pair):

| Table | Purpose | Replaces |
|---|---|---|
| `surgery_config` | key/value store | Hardcoded thresholds in `release_alerts.py` |
| `surgery_alert_recipients` | per-`alert_kind` email lists | Implicit role-based query |
| `facilities` | `code`, `label`, `address`, `is_active`, `sort_order` | Hardcoded `FACILITY_LABEL` dicts in frontend + backend |
| `surgery_procedure_templates` | `code`, `name`, `procedure_kind`, `default_duration_minutes`, `default_cpt_code`, `is_active` | Implicit defaults baked into `procedure_kind` |

Endpoints (all gated on `user:manage`):

- `GET/PUT /api/surgery/config` — single payload of all keys (round-trips the whole config object)
- `GET/POST/DELETE /api/surgery/admin/alert-recipients` — recipient lists
- `GET/POST/PATCH/DELETE /api/surgery/admin/facilities`
- `GET/POST/PATCH/DELETE /api/surgery/admin/procedure-templates`

Picklist endpoints (claim:read):

- `GET /api/surgery/picklists/facilities` — list of active facilities, used by anywhere the frontend currently uses `FACILITY_LABEL`.
- `GET /api/surgery/picklists/procedure-templates` — list of active procedure templates, used by Section 6's coordinator override modal and patient flow.

### Frontend

- `SurgeryRules.jsx` becomes tabbed (top of the page):
  1. **Milestone rules** (existing content)
  2. **Thresholds** — three number inputs for the three release-alert knobs
  3. **Alert recipients** — two table editors (office_release, hospital_release): list emails with add/remove/save
  4. **Facilities** — table with add/edit/delete + active toggle + sort order. Inline-edit pattern (same UX as Insurance Contacts module).
  5. **Procedure templates** — same inline-edit pattern, columns: name, procedure_kind, default_duration_minutes, default_cpt_code, active.
- All tabs gated client-side on `isAdmin`.
- Where the frontend currently uses the hardcoded `FACILITY_LABEL` map, it instead consumes the picklist (cached via React Query for 60s).

### Tests

- `tests/test_surgery_config_admin.py` — covers each of the new endpoints (read, write, delete, permission gating).

---

## Section 6 — Patient self-scheduling + coordinator override

### Goal

When the patient lands on `/p/surgery/:id`, the **earliest eligible slot** is highlighted by default, with a one-click "Confirm this time" button. The patient may pick a different slot. A coordinator from `SurgeryDetail` can also schedule the patient (picking the slot for them) and override the allotted duration. The coordinator can adjust the slot duration after booking too.

### Backend

- The existing `POST /api/p/surgery/:id/pick` endpoint stays (it currently takes a `block_day_id` and books the patient on the first available slot). We add a richer endpoint:

  ```
  POST /api/p/surgery/:id/select-slot
  body: { block_day_id, start_time }
  ```

  Used by the new "pre-selected earliest slot" UI. Server picks the duration from the matching procedure template (Section 5). If none matches, server falls back to the existing `procedure_kind` → duration mapping in `surgery_date_picker.py`.

- New coordinator-side endpoint:

  ```
  POST /api/surgery/:id/schedule
  body: { block_day_id, start_time, duration_minutes?, override_reason? }
  perm: claim:edit
  ```

  Allows custom `duration_minutes` (overrides the procedure template default). `override_reason` is required when `duration_minutes` differs from the template default by >10%.

- New endpoint for post-booking adjustment:

  ```
  PATCH /api/surgery/slots/:slot_id
  body: { duration_minutes, override_reason }
  perm: claim:edit
  ```

  Updates `SurgerySlot.duration_minutes`. Writes a `SurgeryNote` of kind `slot_duration_changed` with before/after/reason.

- Each of the three endpoints writes a `SurgeryNote` to maintain a paper trail:
  - `slot_scheduled` — patient self-served
  - `slot_scheduled_by_coordinator` — coordinator booked on behalf
  - `slot_duration_changed` — duration adjusted on an existing slot

### Frontend

- **`PatientSurgery.jsx`**: when the slot list renders, the earliest available slot is marked with a `Recommended` tag and a prominent "Confirm this time" button. Clicking a different slot updates the highlight; the button text updates to match.
- **`SurgeryDetail.jsx`**: new "Schedule for patient" button (visible when `selected_date` is null + user has `claim:edit`). Opens a modal:
  - Lists the same block days the patient would see.
  - For each slot, shows the procedure template default duration.
  - Coordinator picks a slot → can edit duration → can type an override reason if duration ≠ template default.
- **Slot detail (existing on `SurgeryDetail`)**: after a slot is booked, an "Adjust duration" inline edit appears for coordinators. Same override-reason gate as the modal.

### Tests

- `tests/test_patient_select_slot.py` — patient flow, defaulting duration to template.
- `tests/test_coordinator_schedule.py` — coordinator flow with and without override reason.
- `tests/test_slot_duration_patch.py` — post-booking adjustment.

---

## Phased delivery

Implementation is broken into four phases. Each phase ships independently, each gets its own commit.

| Phase | Sections | Why this order |
|---|---|---|
| **A** | §1 + §4 | Pure UI + small backend additions. Calendar and waitlist columns ship first because they unblock no other work. |
| **B** | §5 | Config + admin UI. Required before §3 can read config-driven thresholds and before §6 can use procedure templates. Big phase but localized to admin. |
| **C** | §3 + §2 | §3 is a small wire-up to the new config. §2 is the new conflict alert. Both surface on the To-do panel. |
| **D** | §6 | Largest phase. Builds on procedure templates from §B. Patient + coordinator scheduling endpoints + UI. |

The implementation plan (next step, `superpowers:writing-plans`) will break each phase into reviewable commits.

## Risks

- **§5 facility migration**: replacing `FACILITY_LABEL` everywhere is error-prone. Plan includes a grep audit step before merging.
- **§3 recipient fallback**: must verify the fallback path keeps working until the office_release/hospital_release lists are populated. Otherwise alerts go silent.
- **§6 duration override**: ill-considered durations can cascade into block-day capacity calculations. Need to confirm the slot-capacity sweep handles arbitrary durations (it should, but verify).
- **§4 urgency backfill**: defaulting all existing surgeries to `routine` is fine, but `expedited` / `urgent` may need a manual triage pass; user accepts this.
