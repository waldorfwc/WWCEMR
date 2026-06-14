# Admin Reorg — Manager nav removal, consent split, page moves, Training top-nav

> **For agentic workers:** subagent-driven-development. Each frontend nav/route change risks the circular-init white-screen — keep `MODULE`/`TIER` references INSIDE render-time functions, never top-level const. Headless `vite preview` load before deploy. Backend TDD; suite baseline 69 failed / 0 errors (must not increase).

**Branch:** `feat/admin-reorg` off `main`.

**Goal:** (1) Remove the duplicate "Manager" entry from the main sidebar (it stays inside the Checklist nav). (2) Move admin-console buttons that belong to a module into that module: Training cards → new Training top-nav; LARC Pharmacies → already in LARC Settings (drop the dup button); Templates (checklist) → already in Checklist nav (drop the dup); Message Templates + Google Sync → Surgery Settings tabs; Consent Templates → SPLIT: surgical → Surgery Settings, device (Nexplanon/Mirena/Skyla/Kyleena/Paragard) → LARC Settings. (3) Add a Training top-level nav module like the others.

**Keep in Admin (core/global):** Permissions, Practice Settings, Add User, Sync RingCentral.

---

## T1 — Remove Manager from main sidebar (frontend, trivial)
**File:** `frontend/src/routes.jsx` (~line 180).
The `/manager-dashboard` route currently carries `nav: { label: 'Manager', order: 90 }`. Delete ONLY the `nav` key — keep the route + `module: M.MY_CHECKLIST, tier: TIER.MANAGE` (it's still linked from `ChecklistNav` as "Manager Dashboard").
```jsx
{ path: '/manager-dashboard', element: <ManagerDashboard />, module: M.MY_CHECKLIST, tier: TIER.MANAGE },
```
Verify: `npm run build` clean; `grep -n "label: 'Manager'" routes.jsx` → no match.
Commit: `feat(nav): drop duplicate Manager entry from main sidebar (T1)`

---

## T2 — Consent template `category` field (backend, TDD)
**Files:** `backend/app/models/surgery.py` (ConsentTemplate ~line 772), `backend/app/database.py`, `backend/app/routers/consent_templates.py`, test.

1. Model: add column after `is_active`:
   ```python
   category = Column(String(20), nullable=False, default="surgical")  # 'surgical' | 'larc'
   ```
2. `database.py` `_apply_lightweight_migrations()` `needed` list: add
   ```python
   ("consent_templates", "category", "VARCHAR(20) DEFAULT 'surgical'"),
   ```
3. Backfill: add a new idempotent migration fn `_migrate_consent_template_category()` called from the migration runner (near `_migrate_template_targeting()` call site, ~line 44). For each ConsentTemplate where `category` is null/empty OR still default but name/procedure_match contains a LARC keyword, set `category='larc'`. LARC keywords (case-insensitive, match against `name` + each `procedure_match` entry): `nexplanon, mirena, skyla, kyleena, paragard, liletta, iud, implant, larc`. Everything else stays `surgical`. Idempotent (only updates rows still needing it). Use a guard: skip entirely if the column doesn't exist yet (inspect).
4. `consent_templates.py`:
   - `ConsentTemplateIn`: add `category: Literal["surgical", "larc"] = "surgical"`.
   - `GET /consent-templates`: add optional query param `category: Optional[str] = None`; when provided and in {surgical,larc}, filter `ConsentTemplate.category == category`. Keep existing order. Include `category` in the serialized response dict.
   - `POST` and `PUT`: persist `category` from the payload.
   - Keep all gates at `Module.SURGERY, Tier.MANAGE` (super-admins manage both; LARC consent management via this endpoint also requires Surgery MANAGE — acceptable, note in commit body).
5. Test `backend/tests/test_consent_template_category.py` (client fixture = super-admin):
   - POST a template with `category="larc"` → GET `?category=larc` returns it, GET `?category=surgical` does not.
   - POST without category → defaults `surgical`.
   - GET with no param → returns all.
   - The serialized payload includes `category`.
Suite ≤ baseline. Commit: `feat(consent): add category field (surgical|larc) + filtered GET + keyword backfill (T2)`

---

## T3 — AdminConsentTemplates: `embedded` + `category` props (frontend)
**File:** `frontend/src/pages/AdminConsentTemplates.jsx`.
1. Signature → `export default function AdminConsentTemplates({ embedded = false, category = null })`.
2. Header block (~lines 417–437): wrap the back-`<Link to="/admin">` + `<h1>`/description in `{!embedded && ( ... )}`. KEEP the "New template" button visible in both modes.
3. Query: include category in the key and request — `queryKey: ['consent-templates', category]`, `queryFn: () => api.get('/consent-templates', { params: category ? { category } : {} }).then(r => r.data)`.
4. Create: when `category` prop is set, new templates default to it. In `TemplateForm`, add `category` to the form state (`initial?.category || category || 'surgical'`) and include it in the POST/PUT body. When `category` prop is set (embedded module view), do NOT render a category selector (it's implied); when not embedded (standalone), render a small `surgical`/`larc` select so the standalone page can still set it.
5. Invalidate `['consent-templates']` (all categories) after mutations.
Verify: `npm run build` clean.
Commit: `feat(consent): embedded + category props for module-scoped consent editing (T3)`

---

## T4 — `embedded` prop on StaffMessageTemplates + AdminGoogleSync (frontend)
**Files:** `frontend/src/pages/StaffMessageTemplates.jsx`, `frontend/src/pages/AdminGoogleSync.jsx`.
- StaffMessageTemplates: signature `({ embedded = false })`; the outer wrapper is `<div className="p-4 max-w-4xl">` with an `<h1 className="page-title">Message Templates</h1>` — when embedded, drop the `p-4 max-w-4xl` padding wrapper down to a plain `<div>` and hide the `<h1>` (keep the "+ New" button). Simplest: wrap the `<h1>` in `{!embedded && ...}` and change the container class to `embedded ? '' : 'p-4 max-w-4xl'`.
- AdminGoogleSync: signature `({ embedded = false })`; wrap the back-`<Link to="/admin">` + `<h1>Google Workspace sync</h1>` + description `<p>` in `{!embedded && (...)}`. KEEP the "Run sync now" button.
Verify: `npm run build` clean.
Commit: `feat(settings): embedded prop on message-templates + google-sync pages (T4)`

---

## T5 — Surgery Settings: Consent / Messages / Google Sync tabs (frontend)
**File:** `frontend/src/pages/SurgerySettings.jsx`.
1. Imports: `import AdminConsentTemplates from './AdminConsentTemplates'`, `import StaffMessageTemplates from './StaffMessageTemplates'`, `import AdminGoogleSync from './AdminGoogleSync'`.
2. `TABS` array — append:
   ```js
   { id: 'consent',   label: 'Consent Templates' },
   { id: 'messages',  label: 'Message Templates' },
   { id: 'gsync',     label: 'Google Sync' },
   ```
3. Render block — append:
   ```jsx
   {tab === 'consent'  && <AdminConsentTemplates embedded category="surgical" />}
   {tab === 'messages' && <StaffMessageTemplates embedded />}
   {tab === 'gsync'    && <AdminGoogleSync embedded />}
   ```
Verify: `npm run build` clean.
Commit: `feat(surgery-settings): add Consent / Message Templates / Google Sync tabs (T5)`

---

## T6 — LARC Settings: device Consent Templates tab (frontend)
**File:** `frontend/src/pages/LarcSettings.jsx`.
1. Import `AdminConsentTemplates`.
2. `TABS` — append `{ id: 'consent', label: 'Consent Templates' }`.
3. Render — append `{tab === 'consent' && <AdminConsentTemplates embedded category="larc" />}`.
Verify: `npm run build` clean.
Commit: `feat(larc-settings): add device Consent Templates tab (T6)`

---

## T7 — Training matrix/reads gated at Module.TRAINING VIEW (backend, TDD)
**File:** `backend/app/routers/training.py`.
The dashboard reads are currently `Depends(get_current_user)` (any authed user). Gate the MANAGER-FACING reads at `requires_tier(Module.TRAINING, Tier.VIEW)`:
- `GET /matrix` (~line 409) → VIEW
- `GET /trainers` (~line 142) → VIEW
- `GET /certifications` (~line 346) → VIEW
LEAVE the personal endpoints on `get_current_user` (any user must reach their own training): `GET /mine`, `GET /mine/responsibilities`, `GET /mine/responsibilities.pdf`, `PATCH /certifications/{id}/acknowledge`, `POST /certifications` (trainer path). Confirm `Module`, `Tier`, `requires_tier` are imported.
Test `backend/tests/test_training_gates.py`: super-admin `client` 200 on `/matrix`, `/trainers`, `/certifications`; a non-super-admin fixture lacking Training tier → 403 on `/matrix`; `/mine` still 200 for any authed user.
Suite ≤ baseline. Commit: `feat(training): gate matrix/trainers/certifications reads at TRAINING VIEW (T7)`

---

## T8 — Training top-level nav (frontend) — ⚠️ render-time nav items
**Files:** create `frontend/src/components/training/TrainingNav.jsx`; `frontend/src/routes.jsx`; `frontend/src/pages/AdminTraining.jsx`; `frontend/src/pages/AdminTrainingCards.jsx`.

1. `TrainingNav.jsx` — copy the FIXED SurgeryNav/MarketingNav pattern (render-time `navItems()`, NO top-level const referencing TIER/MODULE, `<Outlet/>`, `navClass` copied verbatim). Items:
   ```
   Overview → /training        TIER.VIEW   end:true
   Cards    → /training/cards   TIER.MANAGE
   ```
   Gate: `tier(MODULE.TRAINING, it.tier)`. No action button.
2. `routes.jsx`:
   - Eager import: `import TrainingNav from './components/training/TrainingNav'` (AdminTraining/AdminTrainingCards already imported).
   - Add layout route (place near Surgery/LARC, e.g. after Pellets or before Charts):
     ```jsx
     // ── Training ───────────────────────────────────────────────────
     { path: '/training', element: <TrainingNav />, module: M.TRAINING, tier: TIER.VIEW,
         nav: { label: 'Training', order: 85 },
         children: [
       { index: true,    element: <AdminTraining />,      module: M.TRAINING, tier: TIER.VIEW },
       { path: 'cards',  element: <AdminTrainingCards />,  module: M.TRAINING, tier: TIER.MANAGE },
     ]},
     ```
   - REMOVE the two flat routes `/admin/training` and `/admin/training/cards` (the superAdmin element routes) and REPLACE with redirects:
     ```jsx
     { path: '/admin/training',        element: <Navigate to="/training" replace /> },
     { path: '/admin/training/cards',  element: <Navigate to="/training/cards" replace /> },
     ```
3. `AdminTraining.jsx`: add `({ embedded = false })`; wrap the back-`<Link to="/admin">` in `{!embedded && ...}`; KEEP the title/description and controls. Change the internal "Card view" link target `/admin/training/cards` → `/training/cards`.
4. `AdminTrainingCards.jsx`: add `({ embedded = false })`; wrap back-`<Link to="/admin">` in `{!embedded && ...}`; KEEP title. The "Checklist Templates" link to `/admin/templates` stays (checklist templates remain in admin). No other path change needed (it has no other /admin/training links).
5. In the layout route children, render `<AdminTraining embedded />` and `<AdminTrainingCards embedded />` so the page headers don't double up with the top-nav. (Update the children elements above to pass `embedded`.)
VERIFY: `npm run build` clean; `grep -nE "TIER\.|MODULE\." src/components/training/TrainingNav.jsx` → all inside functions; no leftover flat `/admin/training` element routes (only redirects); index route present.
Commit: `feat(training): top-level Training nav (Overview + Cards) + redirect old admin URLs (T8)`

---

## T9 — Admin.jsx cleanup (frontend)
**File:** `frontend/src/pages/Admin.jsx` (~lines 645–684).
Remove these 6 `<Link>` buttons from the User Management header cluster: Templates (`/admin/templates`), Consent Templates, Message Templates, Training (`/admin/training/cards`), Google Sync, LARC Pharmacies. KEEP: Sync RingCentral, Permissions, Practice Settings, Add User.
Drop now-unused icons from the lucide import: `FileSignature`, `MessageSquare` (verify with grep they're not used elsewhere in the file; `CheckSquare`/`Settings`/`Shield` are reused — keep).
Add redirects in routes.jsx for the now-button-less standalone pages so old bookmarks land somewhere sensible (the pages still exist, embedded in settings):
```jsx
{ path: '/admin/consent-templates', element: <Navigate to="/surgery/settings" replace /> },
{ path: '/admin/message-templates', element: <Navigate to="/surgery/settings" replace /> },
{ path: '/admin/google-sync',       element: <Navigate to="/surgery/settings" replace /> },
{ path: '/admin/larc-pharmacies',   element: <Navigate to="/larc/settings" replace /> },
```
Keep `/admin/templates` route as-is (checklist nav links to it). Keep `/admin/consent-templates` etc. page components (used embedded).
NOTE: removing the flat element routes for consent/message/gsync/larc-pharmacies means the AdminConsentTemplates/StaffMessageTemplates/AdminGoogleSync/AdminLarcPharmacies imports in routes.jsx may now be unused EXCEPT where the settings tabs import them directly (settings tabs import from their own files, not routes.jsx). Remove the now-unused routes.jsx imports for any page no longer referenced in routes.jsx (check each: AdminConsentTemplates, StaffMessageTemplates, AdminGoogleSync, AdminLarcPharmacies). Leave AdminTraining/AdminTrainingCards imports (used by /training).
Verify: `npm run build` clean; grep confirms no dangling imports.
Commit: `feat(admin): remove module-owned buttons from admin console; redirect legacy URLs (T9)`

---

## T10 — Headless smoke + deploy
1. `npm run build`; `npx vite preview --port 4178`; Playwright load `/training`, `/surgery/settings`, `/larc/settings`, `/marketing` → each routes to `/login` (unauthed) with 0 console errors (rules out circular-init crash). Close preview.
2. Merge to main; build backend + frontend (`--tag=`), deploy both, `--to-latest` if needed.
3. Post-deploy smoke: frontend `/training` 200; `GET /api/training/matrix` no-auth → 401; `GET /api/consent-templates?category=larc` no-auth → 401; old `/admin/training` redirects.

## Out of scope
- No deep-link-to-tab (`?tab=`) support; legacy URLs redirect to the settings page default tab.
- Checklist templates (`/admin/templates`) stay where they are (already surfaced in Checklist nav).
- Per-category auth split on consent endpoints (kept at Surgery MANAGE; super-admins manage both).
