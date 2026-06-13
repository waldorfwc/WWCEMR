# Surgery Audit Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. TDD each bug: write a failing test reproducing it, fix, verify. Steps use `- [ ]`.

**Goal:** Fix the 32 findings from `docs/surgery-system-audit-2026-06-13.md` (the audit doc is the spec — it has file:line + suggested fix for each `#N`).

**Branch:** `fix/surgery-audit` (off `main`). Backend venv at `backend/venv`. Regression baseline: full suite **87 failed / 7 errors** (pre-existing) — must not increase. Frontend `npm run build` clean.

**Design decision (unreachable steps):** the milestone cutover removed the only writers for several step-completion columns. Fix by making those columns PATCH-able via `SurgeryPatch` + minimal inline controls in the matching StepCard (no milestone resurrection). Where a clean derive from an existing column exists, prefer that.

Tasks are ordered by priority. Each is one subagent dispatch; commit after each.

---

## F1 — Critical: patient-claim capacity bypass + duration bug (#1, #2)

**Files:** `backend/app/services/surgery/self_schedule.py` (`claim_slot_for_patient` ~87-198, `_default_duration_for` ~69-84), check callers in `patient_surgery.py`/`patient_portal.py`.

- #2 first (it's a dependency of #1's correctness): in `_default_duration_for`, translate `block_day.block_kind` → procedure_kind before the template/`DURATIONS` lookup, OR key off `surgery.procedure_classification` (as the slot insert already does at ~159). Use the existing `_block_kind_to_proc_kind()` (in `waitlist.py`) if present.
- #1: in `claim_slot_for_patient`, before inserting the `SurgerySlot`, (a) verify `block_day.facility in (surgery.eligible_facilities or [])` → reject with a clear error if not, and (b) enforce capacity via `can_fit(db, block_day, procedure_kind)` (or route the write through `book_slot()`), using the surgery's real `procedure_classification` as the kind. Keep the existing blackout + overlap checks.

**TDD:** test that a patient claim onto a full block (capacity exceeded) is rejected; a claim onto an ineligible facility is rejected; a valid claim still succeeds; a 240-min robotic stores duration 240 not 60.

Commit: `fix(surgery): patient self-schedule enforces capacity + eligible-facility; correct slot duration`

---

## F2 — Steps reachability, pure-logic (#3, #6, #17, #15)

**Files:** `backend/app/services/surgery/step_engine.py`, `backend/app/routers/surgery.py` (`_surgery_dict`).

- #3: `_state('bill')` → `done` when `s.billed_at` (not `payment_posted_to_billing`). Matches `_STEP_DONE_TIMESTAMPS["bill"]`.
- #6: `_state('path_report')` → read `s.pathology_status` (done when `in ('completed','received')`, n/a when `'not_required'`) instead of `operative_report_status`. Confirm the actual `pathology_status` values from the model/frontend first.
- #17: add `"pathology_status": s.pathology_status` to `_surgery_dict` output.
- #15: fix `_entered_at` — anchor on the most recent completion timestamp among ALL done steps; if the current step has no preceding stamp, fall back to `updated_at`/`created_at`. (Don't let a weeks-old `benefits_verified_at` anchor a later step.)

**TDD:** extend `test_step_engine.py`: billed surgery → bill done; pathology received → path_report done; `_entered_at` returns a recent anchor for a late-stage surgery (not the oldest stamp).

Commit: `fix(steps): bill/path_report read correct columns; emit pathology_status; saner _entered_at`

---

## F3 — Steps reachability, add writers + controls (#4, #5, #13, #14, #16)

**Files:** `backend/app/routers/surgery.py` (`SurgeryPatch`, boarding-slip send endpoint), `backend/app/services/surgery/step_engine.py` (only if deriving), `frontend/src/pages/SurgeryDetail.jsx` (the matching StepCard bodies).

- #5 (cleanest, no new field): in the boarding-slip / "post to hospital" send endpoint, set `s.calendar_invite_sent_at = now_utc_naive()` on success. (That's what step `post_to_hospital` reads.)
- #4 labs: add `labs_sent_to_hospital` (bool) to `SurgeryPatch`; add a "Mark labs sent to hospital" checkbox in `LabsCardBody`.
- #13 welfare_fu: add `post_op_call_status` to `SurgeryPatch`; add a "Spoke to Pt." control (button/select) in the welfare F/U step body. (Closes the previously-logged follow-up.)
- #14 notes_reports: add `operative_report_status` to `SurgeryPatch`; add a status select (e.g. pending/received/completed/not_required) in the Notes & Reports step body. (Don't auto-set on file upload — explicit is clearer.)
- #16 device: add `device_required` + `device_assigned` to `SurgeryPatch`; add a small "Device required / assigned" control in the Allocate Device step body.

Validate enum-ish string fields server-side where the column has known values (mirror existing patterns). TDD: PATCH each field round-trips and flips the corresponding step state.

Commit: `fix(steps): make labs/post-op-call/op-report/device/post-to-hospital completable from the UI`

---

## F4 — Manager escalation sweep on steps (#9)

**Files:** `backend/app/services/surgery/escalations.py`.

Reimplement `run_escalation_sweep` (+ its `_current_milestone`/`_milestone_age_days` private copies) on `step_engine.is_behind`/`current_step` instead of `Surgery.milestones`. Preserve the digest output shape (email/Slack). TDD: a behind-schedule surgery with no milestone rows is picked up by the sweep.

Commit: `fix(escalations): manager behind-schedule sweep runs on step engine, not retired milestones`

---

## F5 — Live UI breaks (#11, #12)

**Files:** `frontend/src/pages/LarcDeviceTypes.jsx` (~110, 236, 241), `frontend/src/pages/SurgeryDetail.jsx` (~4520).

- #11: pass/consume a single consistent name — `<DeviceTypeForm templates={bsTemplates} />` and reference `templates` inside the form. Fixes the ReferenceError on every Add/Edit.
- #12: align the "Refresh from BoldSign" URL to the real backend route `/api/surgery/admin/consent/boldsign-sync/{surgery_id}` (confirm the exact path in `surgery.py`/`boldsign` router first).

Build check; commit: `fix(ui): LARC device-type dialog crash; BoldSign-sync button 404`

---

## F6 — Settings/config integrity (#7, #8, #27, #29)

**Files:** `backend/app/routers/surgery_config.py` (`put_config`, `ConfigPayload`), `frontend/src/pages/SurgerySettings.jsx` (StepsTab save).

- #7: stop the partial-dict wipe. Either (a) StepsTab sends the full merged dict (catalog defaults + existing config + draft) on save, or (b) `put_config` merges dict-valued keys (`step_expected_days_*`, `step_titles_*`) into the existing row instead of replacing. Do (b) server-side (robust regardless of client) AND (a) is optional. TDD: save key A, then save key B, GET returns both.
- #8: add a `reminder_lead_days` validator — non-empty list, each `1 <= d <= 60` (treat `[]` as 422; if "disable reminders" is wanted later, use an explicit flag).
- #27: validate `FacilityCapacity.options` non-empty when `kind in ('robotic','mix_exclusive')`.
- #29: either enforce `case_minutes` in the office fixed_slots capacity calc, or remove it from the schema (prefer removing — slot-count is the real cap). Pick one; note it.

Commit: `fix(surgery-settings): merge dict config on save; validate reminder_lead_days + capacity options`

---

## F7 — Booking/import correctness (#10, #18, #19, #20, #28)

**Files:** `backend/app/services/surgery/candidate_import.py`, `backend/app/routers/surgery.py` (bulk-import tier).

- #10 + #20: `_force_book` uses true interval-overlap (reuse `overlapping_slot`/`slot_conflict`) + block-window bounds, even in backfill; move `CapacityViolation` import to module scope so the backfill except-clause doesn't `NameError`.
- #18: make `APPT_TYPE_MAP` durations match `block_schedule.DURATIONS` (crmc-major 120→180, medstar-minor 60→90, office 30→60) — confirm against DURATIONS.
- #19: resolve the BlockDay by matching imported `appt_time` to the window `start_time`, not `.first()`.
- #28: gate `backfill_mode=true` on `Tier.MANAGE` (or super-admin) instead of WORK.

TDD where practical (overlap detection; duration parity). Commit: `fix(surgery-import): true overlap detection, duration parity, window match, tighten backfill tier`

---

## F8 — Patient-endpoint safety (#24, #25, #26, #30, #31)

**Files:** `backend/app/routers/patient_surgery.py`, `backend/app/services/surgery/self_schedule.py`, `auto_unresponsive.py`.

- #26: `mark_unresponsive` calls `bump_portal_token_version`.
- #30: add the terminal-status guard (used by cancel/schedule) to `/cardiologist`, `/sms-consent`, `/upload-fmla`.
- #24: add the 14-day / 5-business-day window guards to the `/select-slot` + portal `/claim` path (mirror `/pick` + `/reschedule`).
- #25: promote any active non-terminal status (incl. `hold`) to `confirmed` on successful booking (or block `hold` at the gate).
- #31: set `last_patient_activity_at = now` in `claim_slot_for_patient`.

TDD: terminal-status surgery rejects the three patient writes; mark_unresponsive bumps ptv. Commit: `fix(patient-portal): terminal-status guards, ptv bump, reschedule window + status promotion on claim`

---

## F9 — Reminder/money hygiene (#21, #22, #23)

**Files:** `backend/app/services/surgery/reminders.py`, `backend/app/services/surgery/smartsheet_seed.py`.

- #21: format reminder-email date as `strftime("%m/%d/%Y")` (check shared confirmation/payment templates too).
- #22: exclude `status='skipped'` from `_already_sent_for`.
- #23: apply the standard `>50_000 → 0` clamp in smartsheet `_money()`.

TDD: skipped row doesn't suppress a later real send; money >50k clamps to 0. Commit: `fix(surgery): MM/DD/YYYY reminder email, reminder idempotency excludes skipped, seed money clamp`

---

## After all tasks
- Full backend suite ≤ 87 failed / 7 errors; frontend builds clean.
- Redeploy backend + frontend to Cloud Run (same runbook as the prior deploy).
- The unreachable-step fixes (F2/F3) + escalation (F4) are the highest-value: they stop false behind-schedule alerts and revive the manager digest.
