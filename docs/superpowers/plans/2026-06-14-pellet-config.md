# Pellets Module — Config + Settings Page + Shared Top-Nav

> Mirrors the just-shipped LARC effort (which mirrored surgery). REQUIRED: superpowers:subagent-driven-development; TDD backend; build + a HEADLESS pre-deploy load (catches the circular-import white-screen) before deploy.

**Goal:** Give Pellets the same treatment: runtime-configurable thresholds, a `/pellets/settings` page with config tabs, and a shared top-nav on every `/pellets` page.

**Branch:** `feat/pellet-config` off `main`. Backend venv `backend/venv`; suite baseline 69 failed / 0 errors. Frontend `npm run build` clean.

**Mirror these (just-built) equivalents — copy the pattern:**
- Registry: `backend/app/services/larc/settings.py`; Model: `backend/app/models/larc_config.py`
- Config API: the `/larc/config` GET/PUT in `backend/app/routers/larc.py`
- Settings page: `frontend/src/pages/LarcSettings.jsx`
- Nav: `frontend/src/components/larc/LarcNav.jsx` (⚠️ render-time `navItems()` — do NOT reference TIER/MODULE at module-init; that white-screened surgery)

## P1: PelletConfig model + settings registry + table
Create `backend/app/models/pellet_config.py` (`PelletConfig` KV, mirror LarcConfig); `backend/app/services/pellet/settings.py` (`PELLET_SETTINGS_DEFAULTS` + `cfg(db,key)`, mirror larc/settings.py). Register model in `database.py` model-import list (like `larc_config`). Defaults (= current hardcoded):
```
stale_visit_days:        7    # pellet/stale_sweep.STALE_DAYS
dose_suggest_max_pellets: 12  # pellet/dose_suggest.MAX_PELLETS
dose_suggest_max_results: 6   # pellet/dose_suggest.MAX_RESULTS
```
Confirm values vs the real constants. Test `tests/test_pellet_settings.py` (mirror test_larc_settings.py): defaults match constants, cfg default/override/unknown-key. Commit `feat(pellet-config): PelletConfig KV + settings registry (P1)`.

## P2: Thread cfg() through pellet code
Replace runtime reads of `STALE_DAYS` (stale_sweep.py), `MAX_PELLETS`/`MAX_RESULTS` (dose_suggest.py) with `cfg(db,"<key>")` at call sites that have a `db` session (stale_sweep functions take db; dose_suggest's `suggest(...)` — check it has db; if not, thread it from the router endpoint caller). Keep the module-level constants as defaults' source. Grep all usages. Add a parity test (override changes behavior). Suite ≤ baseline. Commit `feat(pellet-config): stale-visit + dose-suggest bounds read from settings (P2)`.

## P3: Validated GET/PUT /pellets/config
Add to `backend/app/routers/pellet.py` (prefix `/pellets`, uses `requires_tier(Module.PELLETS, Tier.X)`): `GET /pellets/config` (VIEW) merged defaults+rows; `PUT /pellets/config` (MANAGE) with `PelletConfigPayload` (stale_visit_days 1–365, dose_suggest_max_pellets 1–50, dose_suggest_max_results 1–50). Mirror the larc /config endpoints. API tests (client fixture): accept valid, 422 out-of-range, roundtrip. Commit `feat(pellet-config): validated GET/PUT /pellets/config (P3)`.

## P4+P5: PelletSettings page + Thresholds tab + Dose Types tab
Create `frontend/src/pages/PelletSettings.jsx` (title "Pellet Settings", tabs: "Thresholds & Windows", "Dose Types"). Thresholds tab = number-field editor on `/pellets/config` (fields: Stale Visit (Days) — "Pre-insertion visits this many days past their scheduled date are swept stale"; Max Pellets Per Combo; Max Dose Suggestions) mirroring LarcSettings ThresholdsTab. Dose Types tab = embed `<PelletDoseTypes embedded />` (add an `embedded` prop to PelletDoseTypes that hides its page header/back-link, like LarcDeviceTypes got). Add flat route `/pellets/settings` (M.PELLETS, MANAGE). `api` from `'../utils/api'`. Build clean. Commit `feat(pellet-settings): settings page (Thresholds + Dose Types tabs) (P4,P5)`.

## P6: Shared top-nav (layout route) — ⚠️ render-time nav items
Create `frontend/src/components/pellet/PelletNav.jsx` (copy the FIXED LarcNav: render-time `navItems()`, `<Outlet/>`, tier-gated via `useCurrentUser().tier(MODULE.PELLETS,...)`, active-state, an action button matching the landing page's primary create action). Nav items:
```
Patients→/pellets (VIEW, end), Inventory→/pellets/inventory (VIEW),
Counts→/pellets/counts (WORK), Audit→/pellets/audit (VIEW),
Manual→/pellets/manual (VIEW), Settings→/pellets/settings (MANAGE)
```
(Dose Types lives under Settings — not in the bar.)
Convert `/pellets` routes in routes.jsx to a parent layout route `{path:'/pellets', element:<PelletNav/>, module:M.PELLETS, tier:TIER.VIEW, nav:{label:'Pellets', order:80}, children:[ {index:true → PelletPatients}, {path:'inventory'→Pellets}, {path:'counts'→PelletCounts}, {path:'counts/:id'→PelletCountDetail}, {path:'audit'→PelletAudit}, {path:'manual'→PelletManual}, {path:'patients'→PelletPatients}, {path:'patients/:id'→PelletPatientDetail}, {path:'dose-types'→PelletDoseTypes}, {path:'settings'→PelletSettings} ]}` — RELATIVE child paths, preserve each child's tier (counts/counts:id=WORK, dose-types/settings=MANAGE, rest VIEW). Drop the old `/pellets`→/pellets/patients redirect (index now renders PelletPatients). Remove the header button clusters from BOTH `Pellets.jsx` and `PelletPatients.jsx` (keep titles + in-page create-drawer actions that have no nav equivalent). Parent keeps `nav` for the sidebar.
VERIFY: `npm run build` clean; `grep -nE "TIER\.|MODULE\." PelletNav.jsx` → all inside functions; no leftover flat /pellets routes; index route present.
Commit `feat(pellet): shared top-nav across all pellet pages via layout route (P6)`.

## P7: Pre-deploy headless smoke + deploy
1. `npm run build`; `npx vite preview` + load root headlessly (Playwright) — must route to /login with 0 console errors (rules out the circular-init crash) BEFORE deploy.
2. Merge to main, push; build+deploy backend then frontend; `update-traffic --to-latest` if needed; smoke `/api/pellets/config`→401, frontend 200.
3. User does authed `/pellets` click-through. Rollback ready.

## Out of scope
- Pellet milestones / smartsheet semantics — untouched (config only).
- PELLET_LOCATIONS stays a code constant.
- Per-dose-type reorder thresholds stay on the Dose Types editor (already configurable).
