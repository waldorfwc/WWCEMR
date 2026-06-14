# Surgery Intake — split name, surgeon default, assistant surgeon, configurable clearance + device multi-selects, order upload

> subagent-driven-development. Backend TDD; suite baseline 69 failed / 0 errors. Frontend: build clean + headless load before deploy. The intake form lives in a drawer, NOT a routed nav component — no circular-import risk, but still don't reference TIER/MODULE at module-init in any new nav-adjacent file.

**Branch:** `feat/surgery-intake-fields` off `main`.

**Goal:** Extend the "Add New Surgery" manual intake (`ManualCreateDrawer`) with: separate First/Last name; surgeon defaulting to "Aryian Cooke, MD"; optional Assistant Surgeon; Clearance Type multi-select (EKG, Hematology, Cardiology, Pulmonology, General — configurable); Device Required multi-select (Benesta, Liletta, Mirena, Paragard, Skyla, Kyleena — configurable); and an optional order-PDF upload. The two option lists are editable in Surgery Settings.

## Current state (verified)
- Form: `frontend/src/components/surgery/surgeryDrawers.jsx` `ManualCreateDrawer` (~lines 126–516). Has single `patient_name` ("Last, First"); unused `first_name`/`last_name` state keys; `surgeon_primary` dropdown from `picks.surgeons`; no device/clearance/assistant/order fields. Submits `POST /surgery/manual`, then navigates to the created surgery.
- Backend create: `backend/app/routers/surgery.py` `create_manual(payload: ManualSurgeryIn)` (~958–1066); schema `ManualSurgeryIn` (~928–955) already has `first_name`/`last_name` optional.
- Model `backend/app/models/surgery.py` `Surgery`: has `patient_name`, `first_name`, `last_name`, `surgeon_primary`, `surgeon_secondary`, `assistant_surgeon_required`/`assistant_surgeon_name`(+phone/fax), `device_required`/`device_kind`, `clearance_required`/`clearance_status`. NO list columns for clearance/device.
- File attach: `POST /surgery/{surgery_id}/files` (~2637) accepts kinds {prior_auth,op_notes,path_report,clearance,consent,fmla,other} — needs `order` added. `SurgeryFile` model already documents `order` as a valid kind.
- Picklist: `backend/app/services/surgery/picklists.py` `SURGEONS = ["Aryian Cooke, MD"]`.
- Config: `backend/app/services/surgery/settings.py` `SETTINGS_DEFAULTS`; `backend/app/routers/surgery_config.py` `ConfigPayload` (list keys full-replace); frontend `frontend/src/pages/SurgerySettings.jsx` tabbed editors.

---

## B1 — Surgery model: clearance_types + device_types columns
**Files:** `backend/app/models/surgery.py`, `backend/app/database.py`.
Add to `Surgery` (near the existing clearance/device fields):
```python
clearance_types = Column(JSON, nullable=True)   # list[str], e.g. ["EKG","Cardiology"]
device_types    = Column(JSON, nullable=True)   # list[str], e.g. ["Mirena","Paragard"]
```
`database.py` `_apply_lightweight_migrations()` `needed` list — add:
```python
("surgeries", "clearance_types", "JSON"),
("surgeries", "device_types", "JSON"),
```
Verify `import app.main` ok. No test needed for the bare columns (covered by B3). Commit `feat(surgery): clearance_types + device_types list columns (B1)`.

---

## B2 — Configurable option lists in surgery config
**Files:** `backend/app/services/surgery/settings.py`, `backend/app/routers/surgery_config.py`, test.
1. `SETTINGS_DEFAULTS`: add
   ```python
   "clearance_types":       ["EKG", "Hematology", "Cardiology", "Pulmonology", "General"],
   "surgery_device_types":  ["Benesta", "Liletta", "Mirena", "Paragard", "Skyla", "Kyleena"],
   ```
2. `ConfigPayload`: add
   ```python
   clearance_types:      Optional[list[str]] = None
   surgery_device_types: Optional[list[str]] = None
   ```
   with a shared `field_validator` for both: each must be a non-empty list of non-empty, stripped, deduped strings (preserve order; reject empty list and blank entries → ValueError so the handler returns 422). Mirror the `reminder_lead_days_valid` validator style.
3. These are list keys → existing PUT full-replace semantics apply (confirm they're NOT added to `_DEEP_MERGE_KEYS`/`_FACILITY_MERGE_KEYS`).
4. Test `backend/tests/test_surgery_intake_config.py` (client = super-admin): GET `/surgery/config` includes both defaults; PUT a new `clearance_types` list roundtrips; PUT empty list → 422; PUT list with a blank string → 422.
Suite ≤ baseline. Commit `feat(surgery-config): configurable clearance_types + surgery_device_types (B2)`.

---

## B3 — ManualSurgeryIn + create_manual: new fields
**Files:** `backend/app/routers/surgery.py`, test.
1. `ManualSurgeryIn`: add
   ```python
   assistant_surgeon_name: Optional[str] = None
   clearance_types: list[str] = []
   device_types: list[str] = []
   ```
   (`first_name`/`last_name` already present; `surgeon_primary` already present.)
2. `create_manual`: when persisting the Surgery —
   - **Name:** if `first_name` and `last_name` provided, set `patient_name = f"{last_name.strip()}, {first_name.strip()}"` (only if the client didn't already send a composed patient_name; prefer the split values when both present). Always persist `first_name`/`last_name` when provided.
   - **Surgeon default:** if `surgeon_primary` is blank, default to `"Aryian Cooke, MD"`.
   - **Assistant:** if `assistant_surgeon_name` non-blank → set `assistant_surgeon_name` and `assistant_surgeon_required = True`; else leave required False.
   - **Clearances:** persist `clearance_types` (stripped/deduped list or None). If non-empty → set `clearance_required = True` and `clearance_status` to the existing "needed/required" not-cleared value — READ the model + existing usages to use the correct enum string (grep `clearance_status` assignments; do NOT invent a value). If empty → leave defaults.
   - **Devices:** persist `device_types` (stripped/deduped list or None). If non-empty → set `device_required = True` and set `device_kind` to the first selected device (back-compat with the single-string field; or comma-join — match how device_kind is read elsewhere; grep `device_kind` usages and pick the safe option, prefer first item).
3. Test `backend/tests/test_surgery_manual_intake.py` (client): POST `/surgery/manual` with first/last name → created surgery has composed `patient_name` + split fields; blank surgeon_primary defaults to "Aryian Cooke, MD"; `assistant_surgeon_name` set → `assistant_surgeon_required True`; `clearance_types=["EKG","Cardiology"]` → persisted + `clearance_required True`; `device_types=["Mirena"]` → persisted + `device_required True` + `device_kind` set. (Reuse/inspect an existing create_manual test for the required-payload shape.)
Suite ≤ baseline. Commit `feat(surgery): manual intake accepts split name, assistant surgeon, clearance + device lists (B3)`.

---

## B4 — Allow `order` kind on file attach
**File:** `backend/app/routers/surgery.py` (`upload_file`, ~2637).
Add `"order"` to the allowed-kinds tuple (both the docstring/Query description and the validation set). No other change. Quick test in `test_surgery_manual_intake.py` or a focused test: `POST /surgery/{id}/files?kind=order` with a small file → 201 + a SurgeryFile(kind='order') row. Commit `feat(surgery): accept order kind on file attach (B4)`.

---

## F1 — ManualCreateDrawer: new fields + order upload
**File:** `frontend/src/components/surgery/surgeryDrawers.jsx` (`ManualCreateDrawer`).
1. **Name:** replace the single `patient_name` input with **First Name** + **Last Name** inputs bound to `first_name`/`last_name`. Keep sending `patient_name` too, composed as `` `${last_name}, ${first_name}` `` on submit (backend also composes; sending it keeps older readers happy). Update required-validation to require both.
2. **Surgeon:** default `surgeon_primary` state to `"Aryian Cooke, MD"` (the dropdown already lists it). Keep the dropdown.
3. **Assistant Surgeon (optional):** a text input bound to `assistant_surgeon_name` (free text — outside-practice names; not a picklist). Hint: "Optional — outside assisting surgeon."
4. **Clearance Type (multi-select):** fetch options from `GET /surgery/config` → `clearance_types`. Render as a set of toggle chips/checkboxes bound to a `clearance_types` array in form state (mirror the existing `eligible_facilities` button-toggle pattern already in this drawer). Optional (may be empty).
5. **Device Required (multi-select):** same pattern, options from `GET /surgery/config` → `surgery_device_types`, bound to `device_types` array. Optional.
6. **Order upload (optional):** a file input (accept `.pdf`) storing a `File` in local component state (NOT in the JSON payload). On submit success (after `POST /surgery/manual` returns the new surgery id), if a file is selected, `POST /surgery/{id}/files?kind=order` as multipart/form-data, then navigate. Surface upload errors but don't block navigation if the surgery was created (show a non-fatal warning). Use the same `api` instance; for multipart, build a `FormData` and pass appropriate headers (check how `UploadDrawer`/other uploads in this file or repo post multipart — reuse that pattern).
7. Add the new keys to the submit payload: `first_name, last_name, patient_name, assistant_surgeon_name, clearance_types, device_types` (plus existing fields).
Build clean. Commit `feat(surgery-intake): split name, surgeon default, assistant surgeon, clearance + device multi-selects, order upload (F1)`.

---

## F2 — Surgery Settings: editable clearance + device lists
**File:** `frontend/src/pages/SurgerySettings.jsx`.
Add a tab `{ id: 'intake', label: 'Clearances & Devices' }` rendering an editor with two simple string-list sections — "Clearance Types" (bound to config `clearance_types`) and "Device Types" (bound to `surgery_device_types`). Each section: list current values with remove buttons + an add-row input; Save → `PUT /surgery/config` with the edited array(s). Mirror the existing list-editor styling in this file (e.g. the post-op/reminder editors). Surface 422 inline (use the file's `saveErrorMessage` helper). Build clean. Commit `feat(surgery-settings): Clearances & Devices tab to edit intake option lists (F2)`.

---

## F3 — Headless smoke + deploy
1. `npm run build`; `vite preview`; Playwright load `/surgery` + `/surgery/settings` → routes to /login, 0 console errors. (The drawer is auth-gated; the build + load rules out crashes.)
2. Merge to main; build + deploy backend then frontend; smoke `/api/surgery/config` (401 noauth), frontend `/surgery` 200.
3. User authed check: open Add ▾ → New Surgery; first/last name, surgeon prefilled "Aryian Cooke, MD", assistant field, clearance + device chips populated from config, attach an order PDF, submit → surgery created with fields + order file; edit the lists in Settings → reflected in the drawer.

## Out of scope
- No AI parsing on the intake order upload (that's the separate `/surgery/orders/upload` flow); here the order is just attached to the created surgery.
- Surgeon list stays a picklist (single entry); not made into a config list this round (only the default is set).
- device_kind single-string field retained for back-compat; device_types is the new multi-value source of truth.
