# Pellets — configurable labs/mammo validity windows + reason-coded calendar chips

> subagent-driven-development. Backend TDD; suite baseline 69 failed / 0 errors. Frontend build + headless load before deploy.

**Branch:** `feat/pellet-labs-mammo-config` off `main`.

**Goal:**
1. Make the labs-freshness window (14 days) and mammo-validity window (365 days) **configurable** (Pellet Settings).
2. The calendar mammo/labs chips show **why** they're ✗ (not entered / missing values / stale > N days / result not acceptable) instead of a bare ✗.

## Current state (verified)
- Readiness (`backend/app/routers/pellet.py`): `_mammo_ready(p, ref_date)` (~3408, hardcoded 365), `_labs_ready(p, ref_date)` (~3418, hardcoded 14), `_visit_ready(p, active)` (~3430). `_has_lab_value` treats blank/"pending" as missing. `ACCEPTABLE_MAMMO_RESULTS`, `MAMMO_NOT_REQUIRED`.
- `_patient_dict(p, include_visits=False, view_extras=None)` (~3545) builds the payload incl. `active_visit_ready`, `active_visit_mammo_ready`, `active_visit_labs_ready`. Called at 3701, 3872 (list), 3905, 3949, 4174 — **all callers have `db`** (`_patient_dict` itself does not).
- Pellet config: `backend/app/services/pellet/settings.py` `PELLET_SETTINGS_DEFAULTS` + `cfg(db,key)`; `PelletConfigPayload` GET/PUT `/pellets/config` (in `backend/app/routers/pellet.py`); UI `frontend/src/pages/PelletSettings.jsx` Thresholds tab (number-field editor).
- Calendar chip: `frontend/src/pages/PelletPatients.jsx` `CalendarVisitCard` (~487): `labsOk=!!patient.active_visit_labs_ready`, `mammoOk=...`, renders `labs ✓/✗` + a generic title.

---

## B1 — Config keys
**Files:** `backend/app/services/pellet/settings.py`, `pellet.py` (PelletConfigPayload), test.
PELLET_SETTINGS_DEFAULTS add:
```python
"labs_valid_days":  14,    # labs must be within N days of the visit
"mammo_valid_days": 365,   # mammo must be within N days of the visit
```
PelletConfigPayload: add `labs_valid_days: Optional[int] = Field(None, ge=1, le=3650)` and `mammo_valid_days: Optional[int] = Field(None, ge=1, le=3650)`. Test: GET defaults present; PUT roundtrip; out-of-range → 422. Commit `feat(pellet-config): configurable labs + mammo validity windows (B1)`.

---

## B2 — Reason-coded readiness using config windows
**File:** `pellet.py`, test.
1. Add status helpers (return a reason code; `_*_ready` becomes `== "ok"` or in the ok set):
   - `_labs_status(p, ref_date, labs_days) -> str`: `"not_required"` if `p.labs_not_required`; `"none"` if no labs at all (no labs_date AND no values); `"missing_values"` if not all of FSH/TSH/E2 present-and-not-pending; `"no_date"` if values but no labs_date/ref; `"stale"` if `labs_date < ref_date - labs_days`; else `"ok"`.
   - `_mammo_status(p, ref_date, mammo_days) -> str`: `"not_required"` if result == MAMMO_NOT_REQUIRED; `"none"` if no mammo_result; `"unacceptable"` if result not in ACCEPTABLE set; `"no_date"`/`"stale"` (vs mammo_days); else `"ok"`.
   - `_labs_ready(p, ref_date, labs_days)` = status in `{"ok","not_required"}`; `_mammo_ready(...)` likewise. Update `_visit_ready(p, active, labs_days, mammo_days)` to pass the windows.
2. Thread config: add params `labs_days=14`, `mammo_days=365` to `_patient_dict(...)`; use them for the ready/status calls. At each of the 5 callers, compute `cfg(db,"labs_valid_days")` / `cfg(db,"mammo_valid_days")` once and pass in (callers have `db`). For the list endpoint (~3849 `_visit_ready` filter + 3872 dict), compute once before the loop.
3. Payload: add `active_visit_labs_reason` (the `_labs_status` code) and `active_visit_mammo_reason` (the `_mammo_status` code), plus echo `labs_valid_days`/`mammo_valid_days` in the dict (so the chip can say "> N days"). Keep the existing `active_visit_labs_ready`/`active_visit_mammo_ready` booleans (now derived from status).
Tests: a patient with all 3 values dated 20 days before a visit and `labs_valid_days=14` → `active_visit_labs_ready False`, reason `"stale"`; with `labs_valid_days=30` → ready True/`"ok"`; missing E2 → `"missing_values"`; not_required → `"not_required"`/ready. Mammo: result older than `mammo_valid_days` → `"stale"`. Suite ≤ baseline. Commit `feat(pellet): reason-coded labs/mammo readiness from configurable windows (B2)`.

---

## F1 — Pellet Settings fields
**File:** `frontend/src/pages/PelletSettings.jsx`.
Add two number fields to the Thresholds editor (bound to `/pellets/config`): "Labs Valid (Days)" → `labs_valid_days` (hint: "Labs must be drawn within this many days of the visit"), "Mammogram Valid (Days)" → `mammo_valid_days` (hint: "Mammogram must be within this many days of the visit"). Mirror existing field pattern + 422 surfacing. Build clean. Commit `feat(pellet-settings): labs + mammo validity-window fields (F1)`.

---

## F2 — Reason-coded calendar chips
**File:** `frontend/src/pages/PelletPatients.jsx` `CalendarVisitCard`.
Replace the bare `labs ✗`/`mammo ✗` with reason text driven by `patient.active_visit_labs_reason` / `active_visit_mammo_reason` and `patient.labs_valid_days`/`mammo_valid_days`:
- labs: `ok`→"labs ✓"; `not_required`→"labs — n/a" (green); `none`→"labs ✗ none"; `missing_values`→"labs ✗ values"; `no_date`→"labs ✗ no date"; `stale`→"labs ✗ >{labs_valid_days}d".
- mammo: `ok`→"mammo ✓"; `not_required`→"mammo — n/a"; `none`→"mammo ✗ none"; `unacceptable`→"mammo ✗ result"; `no_date`→"mammo ✗ no date"; `stale`→"mammo ✗ >{mammo_valid_days}d".
Keep the green/red tone by ready vs not (not_required = green). Put the full human sentence in the `title` tooltip. Keep chip compact. Build clean. Commit `feat(pellet-calendar): reason-coded mammo/labs chips (F2)`.

---

## F3 — Headless smoke + deploy
1. build + vite preview + Playwright load `/pellets` + `/pellets/settings` → /login, 0 console errors.
2. Merge to main; deploy backend then frontend; smoke (`/api/pellets/config` 401, `/pellets` 200, health 200); push origin.
3. Authed check: a patient with labs > window shows "labs ✗ >Nd"; changing Labs Valid (Days) in Settings flips it; missing-value patient shows "labs ✗ values".

## Out of scope
- The "ready" definition itself (mammo acceptable result, paid, bagged) is unchanged — only the day-windows become configurable + the chip reasons.
