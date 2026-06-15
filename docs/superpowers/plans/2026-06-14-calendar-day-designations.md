# Surgery Calendar — per-day work-assignment designation tag

> subagent-driven-development. Backend TDD; suite baseline 69 failed / 0 errors. Frontend build + headless load before deploy.

**Branch:** `feat/calendar-day-designations` off `main`.

**Goal:** Label every day in the surgery calendar with a single work-assignment tag derived from the block schedule + blackouts:
- **MedStar** — a MedStar block day (hospital surgery day)
- **Charles Regional** — a CRMC block day
- **Office Procedures** — an office block day
- **Office Patients** — a regular working weekday with no block day and no blackout (provider sees E/M patients)
- **Blocked reason** (PTO / Holiday / Facility Closed / …) — a whole-day blackout; show the reason/label

## Designation precedence (per date)
1. Whole-day blackout exists → **blocked**; label = `blackout.label` or reason-map (`holiday→Holiday, pto→PTO, facility_closed→Facility Closed, equipment_down→Equipment Down, other→Blocked`).
2. Else BlockDay(s) exist → facility tag(s): `medstar→MedStar`, `crmc→Charles Regional`, `office→Office Procedures`. If multiple facilities on one date, return all (frontend joins with " + ").
3. Else weekday (Mon–Fri) → **Office Patients**.
4. Else (weekend) → none (no tag).

## Current state (verified)
- `BlockDay` (surgery.py ~536): `facility` (medstar|crmc|office), `block_date`, `block_kind`, times. `GET /surgery/admin/block-dates?start&end` returns just dates (no facility).
- `SurgeryBlackoutDay` (~604): `blackout_date`, `start_time`/`end_time` (null = whole day), `scope`, `reason` (holiday|pto|facility_closed|equipment_down|other), `label`, `facility`, `is_recurring`.
- `GET /surgery/calendar` returns scheduled surgeries (flat list). Frontend `SurgeryCalendar.jsx` groups client-side; monthly view already fetches `block-dates` to dim non-block days; `FACILITY_BADGE` map (lines 11-15).

---

## B1 — Day-designations endpoint
**File:** `backend/app/routers/surgery.py`, test.
Add `GET /surgery/calendar/day-types` (gate `requires_tier(Module.SURGERY, Tier.VIEW)`), params `start` (YYYY-MM-DD), `end` (YYYY-MM-DD), max ~120 days. Query BlockDay rows in range (group facilities by date) and SurgeryBlackoutDay rows in range. For EACH date in [start,end], compute per the precedence above. Return:
```json
{ "2026-06-15": {"type": "medstar", "label": "MedStar", "facilities": ["medstar"], "reason": null},
  "2026-06-16": {"type": "office_procedures", "label": "Office Procedures", "facilities": ["office"], "reason": null},
  "2026-06-17": {"type": "office_patients", "label": "Office Patients", "facilities": [], "reason": null},
  "2026-07-04": {"type": "blocked", "label": "Holiday", "facilities": [], "reason": "holiday"},
  "2026-06-20": {"type": "none", "label": null, "facilities": [], "reason": null} }
```
- `type` ∈ {medstar, crmc, office_procedures, office_patients, blocked, mixed, none}. If multiple facilities → type="mixed", `facilities` lists them, `label` = joined ("MedStar + Office Procedures").
- Blackout selection: prefer a whole-day blackout (start_time IS NULL). A partial-day blackout does NOT override the working designation (still compute block/office-patients) — but include a `partial_blackout` reason string in the payload for that date so the frontend can annotate. (Keep simple: add optional `partial_block_reason`.)
- Weekend detection via the date's weekday (0–4 = Mon–Fri working).
Test `backend/tests/test_calendar_day_types.py` (client): seed a MedStar BlockDay, an office BlockDay, a whole-day PTO blackout, and leave a plain weekday + a weekend in range; assert each date's type/label. (Use the existing BlockDay/SurgeryBlackoutDay models directly via the `db` fixture.)
Commit `feat(surgery-calendar): day-types endpoint (work-assignment designation per date)`.

---

## F1 — Render the designation tag on calendar days
**File:** `frontend/src/pages/SurgeryCalendar.jsx`.
- Add a query for `/surgery/calendar/day-types` over the visible range (monthly grid start/end; weekly start+7), keyed to the range.
- In each day cell (monthly ~lines 273-300 and weekly ~equivalent), render the designation as a small colored chip at the TOP of the cell (above the surgery cards): MedStar / Charles Regional / Office Procedures (distinct tones, reuse/extend `FACILITY_BADGE` tones), Office Patients (muted/neutral tone), Blocked → red chip showing the reason/label. For `type:"none"` (weekend) render nothing. For `mixed`, render the joined label (or two chips).
- Replace the current "no block" dimming logic so it's consistent with the new designation (a day that is "office_patients" or blocked is not "no surgery day" — keep surgery cards rendering as-is beneath the chip). Keep existing surgery cards + "+N more".
- Add a small legend (optional) mapping colors to the 5 designations.
Build clean. Commit `feat(surgery-calendar): show per-day work-assignment designation chip`.

---

## F2 — Headless smoke + deploy
1. build + vite preview + Playwright load `/surgery/calendar` → /login, 0 console errors.
2. Merge to main; deploy backend then frontend; smoke (`/api/surgery/calendar/day-types` 401 noauth, `/surgery` 200, health 200); push origin.
3. Authed check: calendar shows MedStar/CRMC/Office Procedures on block days, Office Patients on regular weekdays, and PTO/Holiday on blackout days.

## Out of scope
- No new manual day-type override (designation derived from block schedule + blackouts).
- Pre-op-visit ("office patients" as preop_date) counting — not used; office-patients = regular working days.
