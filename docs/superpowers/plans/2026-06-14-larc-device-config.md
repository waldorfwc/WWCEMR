# LARC (Device) Module — Config + Settings Page + Shared Top-Nav

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. TDD the backend; build + an AUTHENTICATED runtime smoke before any deploy (lesson from the surgery-nav white-screen). Steps use `- [ ]`.

**Goal:** Give the Device (LARC) module the same treatment surgery got: runtime-configurable workflow settings, a `/larc/settings` page with config tabs, and a shared top-nav on every `/larc` page.

**Branch:** `feat/larc-config` off `main`. Backend venv `backend/venv`; pytest baseline ~69 failed / 0 errors (must not increase). Frontend `npm run build` clean.

**Mirror these surgery equivalents** (read them; copy the pattern):
- Registry: `backend/app/services/surgery/settings.py` (`SETTINGS_DEFAULTS` + `cfg(db,key)`)
- Config model: `backend/app/models/surgery_config.py` (`SurgeryConfig` KV)
- Config API + validation: `backend/app/routers/surgery_config.py` (`/surgery/config` GET/PUT, validators)
- Settings page: `frontend/src/pages/SurgerySettings.jsx` (tabs)
- Shared nav: `frontend/src/components/surgery/SurgeryNav.jsx` (layout route + `<Outlet/>`)

## ⚠️ Critical lesson (circular-import white-screen)
`MODULE`/`TIER` live in `routes.jsx`, which eagerly imports the nav layout → circular. **Never reference `TIER.*`/`MODULE.*` at module-init (top-level const).** In `LarcNav`, build nav items inside a render-time function (see SurgeryNav's `navItems()` after its fix). After deploying any nav/routing change, do an **authenticated** page load (not just `npm run build`) before calling it done.

---

## Phase A — Config backend

### L1: LarcConfig model + settings registry + table migration
**Files:** create `backend/app/models/larc_config.py` (KV table `larc_config`, mirror `SurgeryConfig`: `key` PK String, `value` JSON, `updated_at`, `updated_by`); create `backend/app/services/larc/settings.py` (`LARC_SETTINGS_DEFAULTS` + `cfg(db,key)` exactly like surgery/settings.py); add `larc_config` table create to `backend/app/database.py` (it auto-creates via Base.metadata or the lightweight-migration path — match how surgery_config got created). Register the model where models are imported (so create_all picks it up — check how surgery_config is imported in database.py/models).
Defaults registry (= current hardcoded values from `services/larc/workflow.py`):
```
device_expiry_hold_days: 365
assignment_reallocate_after_days: 180
pharmacy_order_sla_days: 14
checkout_ack_window_hours: 24
```
Test `backend/tests/test_larc_settings.py`: defaults match; cfg returns default when no row; cfg returns DB override; unknown key raises. (Mirror test_surgery_settings.py; use the `db` fixture.)
Commit: `feat(larc-config): LarcConfig KV + settings registry with workflow-constant defaults`

### L2: Thread cfg() through workflow.py
**Files:** `backend/app/services/larc/workflow.py` + its consumers (`routers/larc.py`). Replace reads of `DEVICE_EXPIRY_HOLD_DAYS`, `ASSIGNMENT_REALLOCATE_AFTER_DAYS`, `PHARMACY_ORDER_SLA_DAYS`, `CHECKOUT_ACK_WINDOW_HOURS` with `cfg(db, "<key>")` at the call sites (functions that have a `db` session). Keep the module-level constants as the registry defaults' source of truth OR delete them in favor of the registry (prefer: keep them as fallback values referenced only by settings.py defaults; replace runtime reads with cfg). Where a function lacks `db`, thread it in + update callers (same as surgery T2). Grep all usages first. No behavior change when no config rows exist.
Test: extend test or add parity test that overriding a key changes the computed window. Full suite ≤ baseline.
Commit: `feat(larc-config): workflow windows read from larc settings (expiry-hold, reallocate, pharmacy SLA, checkout-ack)`

### L3: Validated GET/PUT /larc/config
**Files:** `backend/app/routers/larc.py` (or a new `larc_config.py` router — match surgery which used a separate `surgery_config.py`; LARC has one big `larc.py`, so add the endpoints there unless cleaner to split). `GET /larc/config` (Tier VIEW) returns merged defaults+rows; `PUT /larc/config` (Tier MANAGE) with a Pydantic `LarcConfigPayload` validating each: device_expiry_hold_days 1–3650, assignment_reallocate_after_days 1–3650, pharmacy_order_sla_days 1–365, checkout_ack_window_hours 1–720. Reject out-of-range → 422 (verify the app's RequestValidationError handler returns 422, like surgery). API tests via the `client` fixture (admin auth): accept valid, reject out-of-range.
Commit: `feat(larc-config): validated GET/PUT /larc/config`

---

## Phase B — Settings UI

### L4: LarcSettings page skeleton + route
**Files:** create `frontend/src/pages/LarcSettings.jsx` (tabs: "Thresholds & Windows", "Device Types", "Pharmacies"); `routes.jsx` add `/larc/settings` (M.LARC, TIER.MANAGE) — but note Phase C nests larc routes under a layout, so add it as a child there if C is done first; otherwise add flat now and move in C. `api` from `'../utils/api'`. Chrome to match SurgerySettings.jsx. Build + commit.
Commit: `feat(larc-settings): settings page skeleton + route`

### L5: Thresholds & Windows tab
**Files:** `LarcSettings.jsx`. Number-field editor bound to `/larc/config` GET/PUT (mirror SurgerySettings AlertsTab). Fields: Device Expiry Hold (days), Assignment Reallocate After (days), Pharmacy Order SLA (days), Checkout Ack Window (hours) — with one-line hints. Inline 422 error surfacing. Build + manual check + commit.
Commit: `feat(larc-settings): Thresholds & Windows tab`

### L6: Device Types + Pharmacies tabs
**Files:** `LarcSettings.jsx`. Move/compose the existing `LarcDeviceTypes` + `LarcPharmacies` page bodies into tabs (extract their inner components to shared pieces, or render the existing page components inside the tab — simplest: import and render `<LarcDeviceTypes/>`/`<LarcPharmacies/>` as tab panels if they're self-contained; otherwise extract the editor sections). Keep `/larc/device-types` and `/larc/pharmacies` routes working (or redirect them to `/larc/settings`?). DECISION: keep the standalone routes AND surface them as Settings tabs (least disruptive). Reuse the LarcDeviceTypes BoldSign-template fix already in place. Build + commit.
Commit: `feat(larc-settings): Device Types + Pharmacies tabs`

---

## Phase C — Shared top-nav (do this carefully — the white-screen risk lives here)

### L7: LarcNav layout + nested routes
**Files:** create `frontend/src/components/larc/LarcNav.jsx` (mirror the FIXED SurgeryNav: `NavLink` bar + `<Outlet/>`, **nav items built in a render-time function**, tier-gated via `useCurrentUser().tier(MODULE.LARC, ...)`, active-state, an `Add ▾`/`+ Add Device` action). Nav items (tiers mirror current routes): Overview→/larc (VIEW), Devices→/larc/devices (VIEW), Checkouts→/larc/checkouts (VIEW), Owed→/larc/owed (VIEW), Inventory Count→/larc/inventory-count (WORK), EOD→/larc/eod (VIEW), Audit→/larc/audit (MANAGE), Manual→/larc/manual (VIEW), Settings→/larc/settings (MANAGE). (Device Types + Pharmacies now live under Settings, so drop them from the top bar.)
Convert the `/larc` routes in `routes.jsx` to a parent layout route `{ path:'/larc', element:<LarcNav/>, module:M.LARC, tier:TIER.VIEW, nav:{label:'Device Tracking', order:70}, children:[ {index:true → Larc}, {path:'devices'...}, {path:'devices/:id'...}, {path:'checkouts'...}, {path:'owed'...}, {path:'audit'...}, {path:'pharmacies'...}, {path:'device-types'...}, {path:'eod'...}, {path:'inventory-count'...}, {path:'manual'...}, {path:'assignments/:id'...}, {path:'settings'...} ]}` with RELATIVE child paths (App.jsx renderRoutes already supports `index`). Match existing M./TIER. identifiers.
Remove the header link-button cluster from `Larc.jsx` (keep its title); remove now-dead button/import code (grep first).
**VERIFY:** `npm run build` clean; grep LarcNav for any top-level `TIER.`/`MODULE.` use (must be none); reason through each `/larc/*` route resolving via Outlet; sidebar "Device Tracking" entry persists (parent keeps nav).
Commit: `feat(larc): shared top-nav across all device pages via layout route`

---

## Phase D — Deploy + smoke
1. Deploy backend (config) then frontend (nav/settings) via Cloud Build + Cloud Run (`--tag=...`, never `--config`).
2. **Authenticated runtime smoke (required):** load `/larc` and 2-3 sub-pages + `/larc/settings` in a logged-in browser; confirm the nav renders (no white-screen), tabs load, a threshold save round-trips, tier-gating hides MANAGE items for non-managers.
3. If anything white-screens, roll back the frontend revision immediately (the surgery playbook).

## Notes / out of scope
- LARC milestones (LarcMilestone / spawn_milestones) are NOT touched — user asked for configuration, not a steps cutover.
- Reorder thresholds stay per-device-type (already configurable in Device Types) — the Thresholds tab covers the global workflow windows only.
- LOCATIONS/LOCATION_LABELS stay code constants (facility list, not a tunable threshold).
