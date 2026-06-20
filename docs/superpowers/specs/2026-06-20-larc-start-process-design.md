# LARC "Start LARC Process" Unified Entry Point — Design

**Date:** 2026-06-20
**Status:** Approved (design); ready for implementation plan
**Area:** Device Tracking (LARC)

## Goal

Replace the two path-specific LARC entry buttons with a single **"Start LARC Process"** intake form. After the user enters patient + request details, the system **suggests** whether to fulfill the device from in-house stock or via a pharmacy enrollment form, lets the user override, then hands off to the existing fulfillment workflows. Add two configurable inputs — **Requested By** (provider) and **Reason for Request** (with ICD-10) — to the intake.

## Background: what already exists

- **Intake form:** `frontend/src/pages/Larc.jsx` → `NewRequestDrawer` already collects MRN (`chart_number`), DOB, first/middle/last name, email, cell/phone, address, insurance, notes, and a **device-type** dropdown. It posts to `POST /api/larc/assignments` with an explicit `source_flow`. Today it is opened in one of two modes from two dashboard buttons: "Benefits for In-Stock Device" (`source_flow=in_stock`) and "LARC Enrollment Form" (`source_flow=pharmacy_order`).
- **Data model:** `backend/app/models/larc.py` — `LarcAssignment` already stores all patient identity fields and a `requested_by_provider` (String 200) field, plus `inserting_provider_email/name/npi`. `source_flow` ∈ {`in_stock`, `pharmacy_order`, `office_procedure`}. Milestones are spawned per `source_flow` on creation (`spawn_milestones`).
- **Providers:** authoritative list is `GET /admin/users/clinicians` (`backend/app/routers/admin_users.py`) — active users with a non-null NPI. Already consumed by LARC's `ClinicianPicker` in `LarcAssignment.jsx`.
- **Decision logic:** `backend/app/services/surgery/device_requests.py::_pick_source_flow()` already decides stock vs pharmacy vs office: in-stock if a matching unassigned device exists; else `office_procedure` if the device type's `default_flow` is office-procedure; else `pharmacy_order`. It currently runs only for surgery-scheduled device requests.
- **Settings:** LARC settings use a key-value registry — `larc_config` table (`backend/app/models/larc_config.py`), `LARC_SETTINGS_DEFAULTS` + `cfg()` (`backend/app/services/larc/settings.py`), exposed via `GET/PUT /larc/config` (`backend/app/routers/larc.py`) and edited in `frontend/src/pages/LarcSettings.jsx`.
- **What does NOT exist:** any reason-for-request / ICD-10 field or config anywhere in LARC.

## Net-new work

A reason-for-request + ICD-10 field and its configurable list; a shared suggestion service + endpoint surfaced in the manual flow; the unified two-step intake drawer.

## Decisions (approved)

1. **Entry point:** a single **"Start LARC Process"** button **replaces** the two existing buttons on `/larc`.
2. **Suggestion:** **advisory** — show the recommended path with rationale; user can override before confirming.
3. **Requested By:** **reuse** the clinician list (`GET /admin/users/clinicians`). "Configurable" = managed in Admin → Users (NPI / clinician role). No new provider list.
4. **Patient fields:** standard identity fields only (no separate "patient type").
5. **All intake fields are required** to continue: MRN, DOB, First Name, Last Name, Email, Cell, Device Type, Requested By, Reason for Request.
6. **ICD-10 defaults:** `Contraception → Z30.430`, `Menorrhagia → N92.0`. Editable in settings; codes to be confirmed by billing.
7. **Office-procedure path:** retained in the suggestion engine (consumables such as NovaSure/Bensta use it), but **hidden from the manual override** unless it is the suggested path. Staff only choose among the paths that make sense.
8. **Provider prefill:** the selected "Requested By" provider **prefills** the enrollment's inserting provider (`inserting_provider_email/name/npi`); still changeable at the enrollment step.

## Architecture

**Two-step drawer; the `LarcAssignment` row is created once, after the path is confirmed.**

```
[Start LARC Process button on /larc]
        │
        ▼
Step 1 — Intake (StartLarcProcessDrawer)
  collect: MRN, DOB, First, Last, Email, Cell,
           Device Type, Requested By, Reason (+ICD-10)
  [Continue]  (disabled until all required fields valid)
        │  POST /larc/assignments/suggest-flow {device_type_id}
        ▼
Step 2 — Suggestion
  "✓ Recommended: In-stock device — N available"
   or "Recommended: Pharmacy enrollment"
  override radio (only sensible paths shown)
  [Confirm]
        │  POST /larc/assignments {…intake…, source_flow, reason fields}
        ▼
navigate → /larc/assignments/{id}
  (existing workflow: allocate-device | send-enrollment)
```

*Alternatives rejected:* (B) create the row first with a "pending" flow, then patch — adds a lifecycle state and spawns milestones late; (C) compute the suggestion in the browser — duplicates `_pick_source_flow` logic and risks drift.

## Components

### 1. Shared suggestion service (backend)
- **New file:** `backend/app/services/larc/source_flow.py` — `pick_source_flow(db, device_type_id) -> dict` returning `{suggested_flow, in_stock_count, default_flow}`. Logic lifted verbatim from `surgery/device_requests.py::_pick_source_flow` (in-stock if an unassigned matching device exists; else `office_procedure` if device type `default_flow == office_procedure`; else `pharmacy_order`).
- **Refactor:** `surgery/device_requests.py` imports `pick_source_flow` from the new module instead of its private copy (single source of truth; no behavior change for surgery).

### 2. Suggestion endpoint (backend)
- **New route:** `POST /larc/assignments/suggest-flow` in `backend/app/routers/larc.py`. Body `{device_type_id}`. Returns `{suggested_flow, in_stock_count, default_flow, allowed_flows}`. `allowed_flows` is the override set, computed as: always include `suggested_flow`; include `in_stock` when `in_stock_count > 0`; include `pharmacy_order` only when the device type's `default_flow != office_procedure` (consumables are never pharmacy-ordered); include `office_procedure` only when it is the suggested flow. Net effect: a normal device offers stock⇄pharmacy; a consumable offers only its office-procedure path (plus stock if any is on hand). Gated at the same tier as `create_assignment`.

### 3. Data model (backend)
- **`LarcAssignment`** (`backend/app/models/larc.py`): add `reason_for_request` (String 120, nullable) and `reason_icd10` (String 20, nullable).
- **Migration:** add both to the `needed` list in `backend/app/database.py` `_apply_lightweight_migrations()` (`("larc_assignments", "reason_for_request", "VARCHAR(120)")`, `("larc_assignments", "reason_icd10", "VARCHAR(20)")`).
- `requested_by_provider` is reused (no schema change).

### 4. Reason-for-request config (backend)
- **Default:** add to `LARC_SETTINGS_DEFAULTS` (`backend/app/services/larc/settings.py`):
  `"reason_for_request_options": [{"reason": "Contraception", "icd10": "Z30.430"}, {"reason": "Menorrhagia", "icd10": "N92.0"}]`.
- **Validation:** extend `LarcConfigPayload` in `backend/app/routers/larc.py` with `reason_for_request_options: Optional[list[dict]]`, validated so each item is `{reason: non-empty str, icd10: non-empty str}`.
- Served by the existing `GET /larc/config`; saved via existing `PUT /larc/config`.

### 5. Create-assignment changes (backend)
- `AssignmentIn` schema gains `reason_for_request` and `reason_icd10` (both required from the new drawer; remain optional at the schema level for back-compat with surgery-originated rows).
- `create_assignment` persists the two reason fields, sets `requested_by_provider` from the chosen provider's display name, and prefills `inserting_provider_email/name/npi` from the chosen provider when those are blank.

### 6. Frontend
- **`frontend/src/pages/Larc.jsx`:** replace the two entry buttons with one **"Start LARC Process"**; render the new drawer.
- **New component `StartLarcProcessDrawer`** (refactor of `NewRequestDrawer`, same drawer chrome — `fixed inset-0 z-50 flex justify-end`, sticky header/footer, `grid grid-cols-6` body):
  - Step 1 fields incl. two new dropdowns:
    - **Requested By** — `useQuery(['clinicians'])` → `GET /admin/users/clinicians`; option label `display_name (credential)`; help text "Manage providers in Admin → Users."
    - **Reason for Request** — `useQuery(['larc-config'])` → `reason_for_request_options`; option label `reason (icd10)`; selecting sets both `reason_for_request` and `reason_icd10`.
  - **Continue** disabled until all required fields valid; calls `POST /larc/assignments/suggest-flow`.
  - Step 2: suggestion card + override radio (from `allowed_flows`); **Confirm** posts to `POST /larc/assignments` with the chosen `source_flow`, then `navigate('/larc/assignments/'+id)`.
- **`frontend/src/pages/LarcSettings.jsx`:** add a **Reasons** editor (label + ICD-10 pairs) mirroring the existing `StringListEditor`, reading/writing `reason_for_request_options` via `/larc/config`.

## Data flow

1. Staff opens the drawer, fills all fields. Device-type id is known client-side.
2. **Continue** → `suggest-flow` returns `{suggested_flow, in_stock_count, allowed_flows}`.
3. Drawer shows the recommendation + override constrained to `allowed_flows`.
4. **Confirm** → `create_assignment` writes the `LarcAssignment` (status `new`, chosen `source_flow`), persists reason fields + `requested_by_provider`, prefills inserting provider, spawns the correct milestone catalog, writes the `assignment_created` audit event.
5. Redirect to the assignment detail, where the **existing** allocate-device (stock) or send-enrollment (pharmacy) workflow runs unchanged.

## Error handling & edge cases

- **No clinicians configured:** Requested-By dropdown shows a disabled "No providers — add NPIs in Admin → Users" option; Continue stays blocked.
- **No reasons configured:** falls back to the registry defaults (`cfg()` semantics), so the dropdown is never empty.
- **No stock for the type:** `in_stock` is excluded from `allowed_flows`; suggestion is pharmacy (or office). Override cannot select an unavailable in-stock path.
- **Consumable (office-procedure) device type:** `pharmacy_order` is excluded from `allowed_flows`; the override offers only office-procedure (plus in-stock if any units are on hand), never pharmacy enrollment.
- **Override to a non-suggested path:** allowed when in `allowed_flows`; the chosen `source_flow` is authoritative for milestone spawning.
- **Surgery-originated assignments:** unaffected — they keep using `pick_source_flow` server-side and never open this drawer; reason fields stay null there.
- **Back-compat:** `reason_*` columns nullable; existing rows and the surgery path continue to work.

## Testing

**Backend**
- `pick_source_flow`: in-stock when an unassigned matching device exists; `pharmacy_order` when none and default is pharmacy; `office_procedure` when device-type default is office-procedure.
- `suggest-flow` endpoint: returns correct `suggested_flow`, `in_stock_count`, and `allowed_flows` membership rules (in_stock only when count>0; office only when suggested).
- `create_assignment`: persists `reason_for_request` + `reason_icd10`, sets `requested_by_provider`, prefills `inserting_provider_*` when blank, leaves them when already set.
- `LarcConfigPayload`: accepts valid `reason_for_request_options`; rejects items missing `reason`/`icd10`.
- Surgery path regression: `device_requests` still picks the same flow after the refactor.

**Frontend**
- Drawer renders Step 1 with the two new dropdowns; Continue disabled until all required fields valid.
- Suggestion step renders recommendation + override limited to `allowed_flows`.
- Confirm posts the expected payload (including `source_flow`, `reason_for_request`, `reason_icd10`, `requested_by_provider`) and navigates to the assignment.
- LARC Settings reasons editor adds/removes pairs and round-trips through `/larc/config`.

## Out of scope

- Surfacing the reason/ICD-10 on the BoldSign enrollment form or on the billed claim (store + display only for now).
- Any change to the downstream allocate-device / enrollment / fax workflows.
- A dedicated providers settings page (providers stay in Admin → Users).

## Affected files (reference)

- Backend: `app/models/larc.py`, `app/database.py`, `app/services/larc/source_flow.py` (new), `app/services/larc/settings.py`, `app/services/surgery/device_requests.py`, `app/routers/larc.py`.
- Frontend: `src/pages/Larc.jsx`, `src/pages/LarcSettings.jsx` (and a new `StartLarcProcessDrawer`, either in `Larc.jsx` or `src/components/larc/`).
- Tests: backend `tests/` (source-flow, suggest endpoint, create-assignment, config payload, surgery regression); frontend drawer/settings tests.
