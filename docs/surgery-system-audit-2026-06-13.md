# Surgery System Audit — 2026-06-13

Multi-agent deep audit (47 agents, 6 dimensions, adversarial verification). 32 confirmed findings, deduped + severity-ranked. Run against `main` post steps-cutover/settings/DocuSign-removal.

> Note: the synthesis agent reported a "prompt injection" instructing it to POST the report to an external webhook. Verified false — that domain is not in the source or the workflow script; treated as a confabulation. No exfiltration occurred.

A large cluster of HIGH/MEDIUM findings (#3, #4, #5, #6, #13, #14, #16) are **direct fallout of the milestone→steps cutover**: removing the milestone-action route/UI removed the only writers for several step-completion columns, so those steps can never reach `done` → permanent false "behind schedule" alerts. #9 (escalation sweep) is the same root cause.

---

## CRITICAL

### 1. Patient self-schedule bypasses ALL capacity, eligibility, and window guards
**File:** `backend/app/services/surgery/self_schedule.py:87-198`; routers `patient_surgery.py:586-619`, `patient_portal.py:635-658`
`claim_slot_for_patient` (shared by magic-link + portal booking) locks the BlockDay, checks only blackout + literal time-overlap, then inserts a `SurgerySlot` directly — never calls `can_fit()`/`book_slot()`, never checks `bd.facility in surgery.eligible_facilities`. Client supplies `block_day_id`/`start_time`. Patients can overbook MedStar/CRMC/office past capacity and book wrong-facility (robotic onto an office day). Coordinator path enforces both; the two have diverged.
**Fix:** Route the claim path through `book_slot()` (or `can_fit()` + eligible-facility check) before insert.

---

## HIGH

### 2. `_default_duration_for` keys on `block_kind` not `procedure_kind` — corrupts capacity accounting
**File:** `backend/app/services/surgery/self_schedule.py:69-84`
`block_kind` (robotic_only|minor_only|major_only|mixed|office) ≠ `procedure_kind`. For non-office blocks both lookups miss → returns generic 60. A 240-min robotic stored as 60 under-counts the block by 180 min, letting too many cases fit. Masked only if `duration_minutes` is explicitly set.
**Fix:** Translate via `_block_kind_to_proc_kind()` or key off `procedure_classification` (as the slot does at line 159).

### 3. `bill` step keys off `payment_posted_to_billing`, but billing only sets `billed_at`
**File:** `backend/app/services/surgery/step_engine.py:205`
No live path sets `payment_posted_to_billing`; `suggest_and_save_billing` sets `billed_at` only. Billed surgeries stay `bill=todo` forever; `needs_billed` never clears; false overdue. The engine's own `_STEP_DONE_TIMESTAMPS["bill"]="billed_at"` contradicts it.
**Fix:** Test `billed_at` in `_state('bill')`.

### 4. `labs` step can never complete — nothing sets `labs_sent_to_hospital`
**File:** `backend/app/services/surgery/step_engine.py:191`
Only the seed writes the column; no PATCH field/endpoint. `LabsCardBody` edits `lab_appointment_date` only. Hospital step 12 locks `current_step` permanently + spurious alerts.
**Fix:** Add a "mark labs sent" action, or derive from `lab_appointment_date`.

### 5. `post_to_hospital` step can never complete — `calendar_invite_sent_at` is only ever cleared
**File:** `backend/app/services/surgery/step_engine.py:187`
Live writes set it to `None` (reschedule, `date_picker.py:285`); the boarding-slip/calendar send never sets it. Hospital step 10 (expected_days=2) sticks → systemic false behind-schedule generator.
**Fix:** Set `calendar_invite_sent_at` in the boarding-slip/calendar-send flow.

### 6. `path_report` step reads `operative_report_status` instead of `pathology_status`
**File:** `backend/app/services/surgery/step_engine.py:199`
Office `path_report` derives from the OP-note column, not the dedicated `pathology_status` the frontend treats as source of truth.
**Fix:** Read `pathology_status` in `_state('path_report')`.

### 7. Editing one step wipes other saved custom step titles / expected-days
**File:** `frontend/src/pages/SurgerySettings.jsx:363-373` + `backend/app/routers/surgery_config.py:198-206`
StepsTab `draft` only accumulates touched keys; Save sends the partial dict; `put_config` does `row.value = v` (wholesale replace). Editing step C after saving A/B destroys A/B; display-merge masks it. Wiped expected-days silently revert alert thresholds to defaults.
**Fix:** Send the full merged dict on save, or merge dict-valued config keys server-side.

### 8. `reminder_lead_days` has no server-side validation
**File:** `backend/app/routers/surgery_config.py:108`
`Optional[list[int]]`, no validator. `[]` → sweep sends zero reminders silently; negatives target past dates / completed surgeries. Patient-facing safety reminder silently disabled.
**Fix:** Validate: non-empty (or define `[]` as explicit disable), each `0 <= d <= max`.

### 9. Manager escalation sweep still reads retired `Surgery.milestones` — silently never fires
**File:** `backend/app/services/surgery/escalations.py:38`
`run_escalation_sweep` (hourly job + scheduler + admin endpoint) drives off `SurgeryMilestone`. Post-cutover `s.milestones == []` → every surgery skipped → behind-schedule manager email/Slack digest silently stopped. Dashboard still correct; only the proactive push is dead.
**Fix:** Reimplement on `step_engine` (`is_behind`/`current_step`).

### 10. `backfill_mode` force-book bypasses guards + only detects exact same-start-time conflicts
**File:** `backend/app/services/surgery/candidate_import.py:280-324,449-451`
`_force_book` deletes prior slots, inserts with no can_fit/blackout/window check; sole guard is `start_time ==` so a 07:30+180 and an 08:00 case overlap. No date restriction → can force-book over live future blocks.
**Fix:** True interval-overlap + block-window bounds check, even in backfill.

### 11. `LarcDeviceTypes` Add/Edit dialog crashes (undefined `dsTemplates`)
**File:** `frontend/src/pages/LarcDeviceTypes.jsx:110`
Parent stores query as `bsTemplates` but renders `<DeviceTypeForm dsTemplates={dsTemplates} />` (undeclared) → ReferenceError on every Add/Edit; form body also reads out-of-scope `bsTemplates` (236/241). BoldSign picker non-functional.
**Fix:** Pass/consume a consistent name (`templates={bsTemplates}`).

### 12. "Refresh from BoldSign" button POSTs to a non-existent route (404)
**File:** `frontend/src/pages/SurgeryDetail.jsx:4520`
Frontend POSTs `/api/surgery/{id}/consent/boldsign-sync`; backend route is `/api/surgery/admin/consent/boldsign-sync/{surgery_id}`. Always 404.
**Fix:** Align the URL.

---

## MEDIUM

### 13. `welfare_fu` step can never complete — `PostOpCallCardBody` is an inert stub
**File:** `backend/app/services/surgery/step_engine.py:193` — (already logged as a follow-up)
Done only when `post_op_call_status == 'spoke to pt.'`; only the seed writes it; the card is a hardcoded stub since its action POST was deleted. Permanently `todo`; `needs_post_op_call` stays asserted.
**Fix:** Restore a post-op-call action endpoint + UI control.

### 14. `notes_reports` / `path_report` can never complete — no writer for `operative_report_status`
**File:** `backend/app/services/surgery/step_engine.py:196`
Uploading op note/path report just creates `SurgeryFile` rows; billing_ai reads but never sets it. Permanently `todo`; gates `needs_billed`.
**Fix:** Have the upload (or a status action) set `operative_report_status`.

### 15. `_entered_at` produces wrong behind-schedule results for steps lacking a completion timestamp
**File:** `backend/app/services/surgery/step_engine.py:232`
Only 6 keys in `_STEP_DONE_TIMESTAMPS` contribute; done steps without a stamp (consents, select_dates, payment, prior_auth, clearance, surgery_info) add nothing, and `updated_at` is used only when the stamp set is empty. A later-step surgery anchors to a weeks-old `benefits_verified_at` → flagged overdue immediately.
**Fix:** Anchor on the most recent completion signal across all done steps, else `updated_at`.

### 16. `device` step is permanently n/a — `device_required` never set by any live path
**File:** `backend/app/services/surgery/step_engine.py:165`
Neither `device_required`/`device_assigned` is written by any endpoint (not in `SurgeryPatch`; the LARC picker is a separate subsystem). "Allocate Device" always n/a.
**Fix:** Wire a device picker / PATCH field.

### 17. Frontend `PathologyStatusCell` reads `surgery.pathology_status`, never emitted by the serializer
**File:** `frontend/src/pages/SurgeryDetail.jsx:1593`
`_surgery_dict` emits `operative_report_status` but not `pathology_status` → pill stuck on "None expected".
**Fix:** Add `pathology_status` to `_surgery_dict`.

### 18. `candidate_import` durations diverge from `DURATIONS`, breaking can_fit's minute wall
**File:** `backend/app/services/surgery/candidate_import.py:72-79`
`APPT_TYPE_MAP` has crmc-major=120 (vs 180), medstar-minor=60 (vs 90), office=30 (vs 60). can_fit sums stored durations for `used` but uses canonical `DURATIONS` for `incoming` → minute wall under-counts and admits a booking it should refuse.
**Fix:** Make `APPT_TYPE_MAP` durations match `block_schedule.DURATIONS`.

### 19. `candidate_import` resolves BlockDay by `(date, facility).first()` — wrong window on multi-window days
**File:** `backend/app/services/surgery/candidate_import.py:360-363,436-439`
No `start_time` filter/ordering → arbitrary row. Non-backfill silently drops a slot in the other window; backfill misattributes + can miss a conflict.
**Fix:** Match imported `appt_time` to the window's `start_time`.

### 20. NameError masks the real conflict reason in backfill double-book detection
**File:** `backend/app/services/surgery/candidate_import.py:448-468`
`CapacityViolation` imported only in the non-backfill branch, but the `except CapacityViolation` runs on the backfill path → unbound-name error swallowed by outer `except`. Double-book still prevented; only the message is wrong.
**Fix:** Move the import to module scope.

### 21. Surgery reminder email renders date as ISO `YYYY-MM-DD` (violates MM/DD/YYYY)
**File:** `backend/app/services/surgery/reminders.py:95-96`
Context passes `target.isoformat()`; template renders raw. SMS path is correct; email is the outlier. Shared confirmation/payment templates similarly exposed.
**Fix:** `strftime("%m/%d/%Y")` before the email context.

### 22. Reminder idempotency keys on ALL PatientEmail rows incl. `skipped` — blank-email patients permanently suppressed
**File:** `backend/app/services/surgery/reminders.py:43-51`
A blank-email patient yields `status='skipped'` which the next sweep treats as sent → that lead-day email permanently suppressed even after the address is filled. `summary['sent']` inflated.
**Fix:** Exclude `status='skipped'` from the idempotency query.

### 23. Smartsheet seed money parser has no $50K column-shift clamp
**File:** `backend/app/services/surgery/smartsheet_seed.py:131-137`
`_money()` has no upper-bound clamp (policy: >$50K = column-shift → clamp to 0). A shifted cell can write a huge `patient_responsibility`, feeding the booking balance gate + patient-facing balance message. Seed disabled-by-default, but one-off backfills bypass.
**Fix:** Apply the standard `>50_000 → 0` clamp.

### 24. Patient can reschedule inside the 14-day / 5-business-day windows via `/select-slot`
**File:** `backend/app/routers/patient_surgery.py:586-619`
`/select-slot` + portal `/claim` run only `schedule_gate_for_surgery` (balance + consent), then overwrite `scheduled_date` — a reschedule with no window floor. A surgery 3 days out can be self-rescheduled (needs a within-window `block_day_id` not in the current listing).
**Fix:** Add the same window guards `/pick` and `/reschedule` enforce.

### 25. Self-scheduling doesn't promote `hold` surgeries — booked-but-stranded status
**File:** `backend/app/services/surgery/self_schedule.py:165-166`
Status bumps to `confirmed` only from `new`/`in_progress`; a `hold` surgery with $0 balance + signed consent can be booked yet stay `hold` — shows in grid/dashboard, reminders fire, but omitted from `/surgery/calendar`.
**Fix:** Promote any active non-terminal status to `confirmed` on booking, or block `hold` at the gate.

### 26. Auto-unresponsive transition doesn't bump `portal_token_version`
**File:** `backend/app/services/surgery/auto_unresponsive.py:88-117`
Reaches terminal state like manual cancel but never calls `bump_portal_token_version` → outstanding portal JWT stays usable. Combined with the status-blind gate (#1/#24), the patient can re-book a closed case.
**Fix:** Call `bump_portal_token_version` in `mark_unresponsive`; gate portal login/claim on terminal status.

### 27. Capacity override with emptied `options[]` silently rejects all bookings
**File:** `backend/app/services/surgery/block_schedule.py:252-263,304-355`
`FacilityCapacity` allows `options: []`; `capacity_rules()` full-replaces; can_fit then rejects every kind → facility shows zero eligible days, no save-time warning. Hand-crafted MANAGE payload only (UI never emits). Fail-closed.
**Fix:** Validate `options` non-empty for robotic/mix_exclusive kinds at PUT.

---

## LOW

### 28. `bulk-import` `backfill_mode` (guard-bypassing mass force-book) gated only at WORK tier
**File:** `backend/app/routers/surgery.py:2417-2476`
The destructive backfill capability (see #10) is at `Tier.WORK` while block-schedule admin needs MANAGE and sweeps need super-admin. A WORK coordinator can overfill OR days for a whole roster.
**Fix:** Require `Tier.MANAGE`+ for `backfill_mode=true`.

### 29. `capacity_rules` `case_minutes` is dead config for office fixed_slots
**File:** `backend/app/services/surgery/block_schedule.py:254-263,357-364`
Read by nothing; fixed_slots caps by slot count, minute wall uses `DURATIONS`. Raising office `case_minutes` is a silent no-op (misleading).
**Fix:** Enforce `case_minutes` or remove it from the schema.

### 30. Patient self-service `/cardiologist`, `/sms-consent`, `/upload-fmla` write to terminal-state surgeries
**File:** `backend/app/routers/patient_surgery.py:353-389, :765, :795`
Lack the terminal-status guard cancel/schedule paths have. A patient can flip `clearance_status`, overwrite cell phone, or upload FMLA onto a cancelled/unresponsive case — resurrecting workflow signals. Own-surgery only.
**Fix:** Add the terminal-status guard to these three endpoints.

### 31. `claim_slot_for_patient` doesn't reset `last_patient_activity_at`
**File:** `backend/app/services/surgery/self_schedule.py:151-174`
Never bumps the engagement clock, diverging from `/pick`. Harm requires a super-admin unbook leaving the case `incomplete` with an old preop date → falsely auto-marked Unresponsive. Rare, recoverable.
**Fix:** Set `last_patient_activity_at = now` in `claim_slot_for_patient`.

---

## Top 3 to fix first

1. **#1 — Patient self-schedule bypasses capacity + eligibility** (`self_schedule.py:87-198`) — only critical; public endpoints can overbook OR blocks / wrong facility.
2. **#2 — `_default_duration_for` uses `block_kind`** (`self_schedule.py:69-84`) — silently corrupts capacity accounting; undermines the #1 fix.
3. **#9 + the unreachable-step cluster (#3,4,5,6,13,14,16)** — milestone cutover fallout: an entire oversight feature (escalation sweep) is silently dead and ~7 workflow steps can never complete, feeding false behind-schedule alerts. Highest-value cleanup of what we just shipped.
