# LARC Overview, Inventory Export & Provider Management — Design

**Date:** 2026-06-21
**Status:** Approved (design); ready for implementation plan
**Area:** Device Tracking (LARC)
**Build mode:** One combined project (5 small, cohesive changes).

## Goal

Five Device-Tracking improvements: relocate the Start-LARC-Process button next to Add Device, show on-hand device counts by ownership on the Overview, add a regression test confirming unacknowledged-checkouts works, add a CSV/PDF inventory export, and let staff add providers from the Practice Profile settings.

## Decisions (approved)

1. **Ownership counts** cover **on-hand (in-stock) devices only** — status ∈ {`unassigned`, `assigned`, `received`} (same set the existing "on hand by type" cards use).
2. **Inventory export** offers **both CSV and PDF**, scoped to on-hand devices; assignee shown for assigned devices, blank for unassigned.
3. **Add provider** **creates a clinician User** (name + email + NPI + role + credential) — the single source the "Requested By" dropdown already reads. Super-admin only.
4. (Defaults) Start-LARC button moves into the `LarcNav` bar (visible on all LARC pages), **WORK-gated** like Add Device. Unacknowledged-checkouts gets a **regression test** (it already works; no behavior change).

## Background (verified)

- **Dashboard:** `GET /larc/dashboard` (`backend/app/routers/larc.py` ~247-384) tallies on-hand devices (status in `unassigned/assigned/received`) into `on_hand_by_type`/`_location`/`_category`. Frontend Overview = `frontend/src/pages/Larc.jsx`.
- **Devices:** `GET /larc/devices` + `_device_dict` expose `our_id`, `manufacturer_lot`, `expiration_date`, `location`/`location_label`, `ownership`/`ownership_label`, `status`. Active assignee = `LarcAssignment` where `device_id == X and is_active`.
- **Ownership values:** `wwc_owned` (Practice Owned), `patient_owned` (Patient Owned), `wwc_claimed` (Practice Claimed).
- **Export patterns:** CSV — `rows_to_csv()` (`services/larc/reports.py`) + `StreamingResponse` (`larc_reports.py`). PDF — reportlab `build_pdf()` (`services/pellet/inventory_export.py`) + `Response(media_type="application/pdf")` (`routers/pellet.py` `/lots/export.pdf`).
- **Unacknowledged checkouts:** computed from `LarcCheckout` rows (`approval_status=="approved"`, `acknowledged_at is None`, `requested_at <= now - cfg("checkout_ack_window_hours")`); `/checkouts/{id}/acknowledge` (WORK) sets `acknowledged_at`. Verified working; no tests exist.
- **Providers:** clinician = active `User` with `npi` set; listed via `GET /admin/users/clinicians`. Created via `POST /admin/users` (super-admin; payload email+group+display_name) then NPI/role/credential set via `PATCH /admin/users/{email}`. `User.clinician_role ∈ {provider, app}`, `credential ∈ {MD,DO,NP,PA}`.
- **Buttons:** "Start LARC Process" + its `StartLarcProcessDrawer` live in `Larc.jsx`; "+ Add Device" in `LarcNav.jsx` (TIER.WORK-gated NavLink to `/larc/devices?add=1`).

---

## Components

### 1. Relocate "Start LARC Process" (frontend)
- **Extract** `StartLarcProcessDrawer` from `Larc.jsx` into its own file `frontend/src/components/larc/StartLarcProcessDrawer.jsx` (export default), so both the nav and (if ever needed) other places can mount it. No behavior change to the drawer.
- In `LarcNav.jsx`: add a WORK-gated **"Start LARC Process"** button next to "+ Add Device"; it opens the drawer (local `open` state in LarcNav) and on create navigates to `/larc/assignments/{id}`. Visible on all LARC pages.
- In `Larc.jsx`: remove the header button + drawer mount (it now lives in the nav). Keep the rest of the Overview.

### 2. Ownership counts on Overview (backend + frontend)
- **Backend:** in `dashboard()`, add `on_hand_by_ownership` — tally the same on-hand device set (status in `unassigned/assigned/received`) by `d.ownership`, returning `{"wwc_owned": N, "patient_owned": N, "wwc_claimed": N}` (default each to 0).
- **Frontend:** Overview renders three cards — **Practice Owned** (`wwc_owned`), **Patient Owned** (`patient_owned`), **Practice Claimed** (`wwc_claimed`) — using the same card styling as the on-hand-by-type cards.

### 3. Unacknowledged-checkouts regression test (backend test only)
- New `backend/tests/test_larc_unack_checkouts.py`: seed an approved `LarcCheckout` with `requested_at` older than the ack window and `acknowledged_at` null → `GET /api/larc/dashboard` lists it under `unacknowledged_checkouts`; `POST /api/larc/checkouts/{id}/acknowledge` sets `acknowledged_at` and a subsequent dashboard call omits it; a recently-requested approved checkout (within the window) is NOT listed. No production code change.

### 4. Inventory CSV + PDF export (backend + frontend)
- **Rows helper:** a function that returns the on-hand device rows for export — for each device with status in `unassigned/assigned/received`: `our_id`, `device_type` (name), `lot` (`manufacturer_lot`), `expiration_date` (MM/DD/YYYY), `location` (`location_label`), `ownership` (`ownership_label`), `status`, `assignee` (active assignment's `patient_name` + `chart_number`, blank if none). Resolve assignees in one query (map device_id → active assignment) to avoid N+1.
- **CSV:** `GET /larc/devices/export.csv` → `rows_to_csv(rows)` via `StreamingResponse(media_type="text/csv")`, filename `larc-inventory-<date>.csv`. VIEW-gated.
- **PDF:** `GET /larc/devices/export.pdf` → new `backend/app/services/larc/inventory_export.py::build_pdf(rows, generated_by)` (reportlab, mirroring pellet's) → `Response(media_type="application/pdf")`, filename `larc-inventory-<date>.pdf`. VIEW-gated. Writes a HIPAA `inventory_export` audit row (like pellet).
- **Frontend:** on `LarcDevices.jsx`, add "Export CSV" and "Export PDF" buttons that open the export URLs (with the current auth — use the api base; since these are file downloads, trigger via `window.open`/an authed fetch→blob, matching how other LARC/pellet downloads are triggered).

### 5. Add provider in Practice Profile (backend + frontend)
- **Backend:** extend `POST /admin/users` `CreateUserPayload` with optional `npi`, `clinician_role`, `credential`; when provided, set them on the new User at creation (single round-trip). Keep super-admin gating. Default `group="clinical"` when the caller is adding a clinician (the form sends it). Existing callers unaffected (new fields optional).
- **Frontend:** add a **Providers** section to `PracticeSettings.jsx` (the Practice Profile tab, super-admin): list current clinicians (`GET /admin/users/clinicians` → name, NPI, role, credential) and an **"Add Provider"** form (Display name, Email, NPI, Role [Provider/APP], Credential [MD/DO/NP/PA]) → `POST /admin/users` with `{email, display_name, group:"clinical", npi, clinician_role, credential}`; on success refresh the list. The new provider immediately appears in the "Requested By" dropdown (same source).

---

## Data flow notes
- Ownership counts + export both key off the on-hand status set `{unassigned, assigned, received}` — keep them consistent (a shared constant is fine).
- The export's assignee column means assigned devices show a patient; unassigned/received show blank.
- Adding a provider creates a real User (login-capable) — that's intended; the dropdown needs a User row. An email is required.

## Error handling & edge cases
- **No devices on-hand:** export returns an empty CSV/PDF (header only); counts show 0/0/0.
- **Duplicate provider email:** `POST /admin/users` already 409s on an existing email — surface that message in the form.
- **Provider missing NPI:** if NPI is left blank, the User is created but won't appear as a clinician (the dropdown filters on NPI); the form should require NPI to add a *provider*.
- **Export auth:** endpoints are VIEW-gated; the download trigger must send the bearer token (authed fetch→blob, or a token-bearing request) — not a bare `<a href>` that drops auth.
- **Non-super-admin** can't see the Providers section (Practice Profile tab is already super-admin only).

## Testing
- **Backend (pytest):**
  - `dashboard` returns `on_hand_by_ownership` with correct per-ownership on-hand counts (and excludes terminal-status devices).
  - Inventory export rows: correct on-hand set, assignee populated for assigned devices / blank for unassigned, expected columns; CSV endpoint returns text/csv; PDF endpoint returns application/pdf bytes.
  - Unacknowledged-checkouts regression test (section 3).
  - `POST /admin/users` with npi/clinician_role/credential creates a clinician that appears in `GET /admin/users/clinicians`.
- **Frontend:** `npm run build`; manual — button sits by Add Device; 3 ownership cards render; export buttons download; Add-Provider form adds a clinician that shows in Requested By.

## Out of scope
- Editing/deactivating existing providers from Practice Profile (Admin → Users already does that).
- XLSX export (CSV + PDF only).
- Changing the unacknowledged-checkouts behavior (only adding a test).

## Affected/new files
- Backend: `app/routers/larc.py` (dashboard ownership tally; export endpoints), `app/services/larc/inventory_export.py` (new, PDF), `app/routers/admin_users.py` (CreateUserPayload + create logic); tests: `test_larc_dashboard_ownership.py`, `test_larc_inventory_export.py`, `test_larc_unack_checkouts.py`, `test_admin_add_clinician.py` (new).
- Frontend: `components/larc/StartLarcProcessDrawer.jsx` (extracted), `components/larc/LarcNav.jsx`, `pages/Larc.jsx`, `pages/LarcDevices.jsx`, `pages/admin/PracticeSettings.jsx`.
