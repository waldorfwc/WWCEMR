# Patient portal — cancel on all screens + configurable cancellation fee

> subagent-driven-development. Backend TDD; suite baseline 69 failed / 0 errors. Frontend build + headless load before deploy.

**Branch:** `feat/portal-cancel-fee` off `main`.

**Goal:**
1. Let patients cancel from the **pre-scheduling** portal screens too (not just the "You're scheduled!" screen).
2. Make the cancellation fee **configurable**: amount + days-before threshold (today hardcoded $351 / 14 days).
3. Show the cancellation-fee notice **only to patients who meet the criteria** (have a scheduled date AND are within the configured days-before window). Unscheduled patients cancel with no fee notice.

## Current state (verified)
- Patient self-cancel: `POST /p/surgery/{id}/cancel` (`patient_surgery.py` `patient_cancel` ~650). `fee_required = scheduled_date and 0 <= (scheduled_date-today) <= 14`; message hardcodes "$351". Works even with no scheduled_date (then fee_required stays False). Frees slot, voids consents, notifies scheduler, logs activity.
- Staff cancel: `surgery.py` `cancel_surgery` (~2433) — same $351/14-day rule + a MANAGE fee-override.
- Portal status: `GET /p/surgery/{id}/status` (`patient_surgery.py` ~243) returns surgery info; does NOT return fee info or a can_cancel flag.
- Portal UI `frontend/src/pages/PatientSurgery.jsx`: `AlreadyScheduledScreen` (scheduled) has Reschedule (disabled within 14d) + Cancel Surgery buttons. Pre-scheduling screens (SlotPicker, BalanceDueScreen, CardiologistAskScreen, locked "can't pick" block) have NO cancel. `CancelConfirmScreen` (~431) only renders when `scheduled_date` is set (gate at ~215); hardcodes 14-day / $351.
- Surgery config registry: `backend/app/services/surgery/settings.py` SETTINGS_DEFAULTS + `cfg(db,key)`; `backend/app/routers/surgery_config.py` ConfigPayload; Surgery Settings UI `frontend/src/pages/SurgerySettings.jsx` (AlertsTab number-field editor on `/surgery/config`).

---

## B1 — Config keys
**Files:** `settings.py`, `surgery_config.py`, test.
SETTINGS_DEFAULTS add:
```python
"cancellation_fee_amount":      351,   # dollars
"cancellation_fee_days_before": 14,    # cancel within N days of surgery → fee
```
ConfigPayload add `cancellation_fee_amount: Optional[int] = Field(None, ge=0, le=100000)` and `cancellation_fee_days_before: Optional[int] = Field(None, ge=0, le=365)` (scalar full-replace). Test: GET defaults present; PUT roundtrip; out-of-range → 422. Commit `feat(surgery-config): configurable cancellation fee amount + days-before (B1)`.

---

## B2 — Drive both cancel paths + status from config
**Files:** `patient_surgery.py`, `surgery.py`, test.
- `patient_cancel`: replace the hardcoded `14` with `cfg(db, "cancellation_fee_days_before")` and the `$351` message text with `cfg(db, "cancellation_fee_amount")` (format as `${amount}`). `fee_required = bool(s.scheduled_date) and 0 <= (s.scheduled_date - date.today()).days <= days_before`. Pass the amount into the scheduler-notify extra + the returned message.
- `cancel_surgery` (staff): same — use cfg for the days-before rule and the amount in any fee message/override logic. Keep the MANAGE fee-override behavior; just source the numbers from config.
- `patient_status` (`GET /p/surgery/{id}/status`): add to the response:
  - `cancellation_fee_amount`: cfg amount
  - `cancellation_fee_days_before`: cfg days
  - `cancellation_fee_applies`: `bool(scheduled_date) and 0 <= (scheduled_date-today) <= days_before`  ← the "criteria met" flag
  - `can_cancel`: `status not in ("cancelled","completed","unresponsive")`
Tests: with a config override (e.g. amount=500, days=21) a surgery scheduled in 10 days → status `cancellation_fee_applies=True`, amount 500; a surgery with no scheduled_date → `applies=False`, `can_cancel=True`; patient_cancel of an unscheduled surgery → `fee_required=False`. Suite ≤ baseline. Commit `feat(surgery): cancellation fee + portal status read from config; unscheduled cancel = no fee (B2)`.

---

## F1 — Surgery Settings: fee fields
**File:** `frontend/src/pages/SurgerySettings.jsx`.
Add two number fields to the Alerts/Thresholds config editor (bound to `/surgery/config`): "Cancellation Fee ($)" → `cancellation_fee_amount`, "Cancellation Fee Window (days before surgery)" → `cancellation_fee_days_before`, with one-line hints ("Patients who cancel within this many days of surgery are warned about the fee"). Mirror the existing number-field pattern + 422 surfacing. Build clean. Commit `feat(surgery-settings): cancellation fee amount + window fields (F1)`.

---

## F2 — Portal: cancel on all screens + config-driven fee notice
**File:** `frontend/src/pages/PatientSurgery.jsx`.
1. **CancelConfirmScreen:** remove the `scheduled_date`-required gate (render in `mode==='cancel'` regardless). Drive the fee notice from status, NOT hardcoded: `const feeApplies = status.cancellation_fee_applies`; show the amber fee block only when `feeApplies`, using `status.cancellation_fee_amount` and `status.cancellation_fee_days_before` in the copy. When there's no scheduled_date, show cancel/decline copy ("You're cancelling your surgery before scheduling — no fee applies.") and skip the date line. Keep the optional reason + confirm → `POST /p/surgery/{id}/cancel`.
2. **Pre-scheduling screens:** add a subtle "Cancel my surgery" affordance available on the SlotPicker, BalanceDueScreen, CardiologistAskScreen, and the locked "can't pick" screen — e.g. a shared small footer link `Cancel my surgery` that calls `onCancel()` (sets `mode='cancel'`). Only render it when `status.can_cancel`. (Simplest: render the footer in the main component below the screen switch when `mode==='view' && status.can_cancel`, so it appears on every pre-scheduling screen AND the scheduled screen keeps its existing red button — avoid duplicate on the scheduled screen by only adding the footer to the non-scheduled branches, or gate it `!status.scheduled_date`.)
3. **AlreadyScheduledScreen:** replace the hardcoded `<= 14` reschedule-disable window with `status.cancellation_fee_days_before` (the within-window reschedule-by-phone rule should track the same configured window), and the "$351" / "14 days" copy with the configured values.
Build clean. Commit `feat(portal): cancel from any screen + config-driven cancellation-fee notice (F2)`.

---

## F3 — Headless smoke + deploy
1. build + vite preview + Playwright load `/surgery` + `/surgery/settings` → /login, 0 console errors. (Portal is token-gated; can't render headless — verify build + shape.)
2. Merge to main; deploy backend then frontend; smoke (`/api/surgery/config` 401, `/surgery` 200, health 200); push origin.
3. Authed/portal check: a patient with no scheduled date sees a Cancel option with NO fee notice; a scheduled patient within the configured window sees the fee notice with the configured $ amount; changing the config in Surgery Settings updates both.

## Out of scope
- No change to how/whether the fee is actually billed (still a flag + staff follow-up); only the threshold/amount + notice are configured.
- Reschedule self-service window stays tied to the same configured days-before (no separate reschedule-window setting this round).
