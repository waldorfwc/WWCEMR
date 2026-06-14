# Recalls Module — Config + Settings Page + Thin Top-Nav (incl. editable outcome taxonomy)

> Mirrors the surgery/LARC/pellet pattern. REQUIRED: superpowers:subagent-driven-development; TDD backend (esp. taxonomy parity); build + HEADLESS pre-deploy load before deploy.

**Goal:** Recalls gets a `/recalls/settings` page (Thresholds & Windows + Outcomes tabs) with runtime-configurable values, and a thin shared top-nav (Overview | Settings) across the recall pages. Recall is single-page, so the nav is thin but consistent.

**Branch:** `feat/recall-config` off `main`. Suite baseline 69 failed / 0 errors. Frontend build clean.

**Mirror:** `app/models/pellet_config.py`, `app/services/pellet/settings.py`, the `/pellets/config` endpoints, `frontend/src/pages/PelletSettings.jsx`, `frontend/src/components/pellet/PelletNav.jsx` (⚠️ render-time `navItems()` — no top-level TIER/MODULE).

## Current state (verified)
`backend/app/routers/recalls.py` hardcodes the outcome taxonomy + windows:
- `PERMANENT_OUTCOMES` = {label: reason_code} (Declined recall→declined, Do not call→do_not_call, Patient deceased→deceased, Left practice→left_practice)
- `COOLDOWN_OUTCOMES` = {label: timedelta} (Left voicemail→3d, No answer→1d, Pending callback→2d)
- `COMPLETED_OUTCOMES` = {"Scheduled"}; plus neutral "Wrong number"
- `CLAIM_TTL = timedelta(minutes=5)` (soft-claim lock)
- an overdue-24-months metric (grep `overdue_24mo` / `24` near line 688) → `overdue_window_months` default 24
Used in: `log_outcome` (POST /{id}/outcome, ~517-590), the `/outcomes/catalog` endpoint (~716-727, already consumed by the frontend outcome picker `recall-outcomes` query), and the claim flow (CLAIM_TTL).

## R1: RecallConfig model + registry + table
Create `backend/app/models/recall_config.py` (`RecallConfig` KV, mirror PelletConfig); `backend/app/services/recall/settings.py` (`RECALL_SETTINGS_DEFAULTS` + `cfg(db,key)`). Register in database.py model-import line. Defaults:
```
claim_ttl_minutes:    5
overdue_window_months: 24    # confirm vs the hardcoded value near line 688
recall_outcomes: [           # the taxonomy, default == current hardcoded sets, in display order
  {"label":"Declined recall","category":"permanent","reason_code":"declined"},
  {"label":"Do not call","category":"permanent","reason_code":"do_not_call"},
  {"label":"Patient deceased","category":"permanent","reason_code":"deceased"},
  {"label":"Left practice","category":"permanent","reason_code":"left_practice"},
  {"label":"Left voicemail","category":"cooldown","cooldown_days":3},
  {"label":"No answer","category":"cooldown","cooldown_days":1},
  {"label":"Pending callback","category":"cooldown","cooldown_days":2},
  {"label":"Scheduled","category":"completed"},
  {"label":"Wrong number","category":"neutral"},
]
```
Test `tests/test_recall_settings.py`: defaults present, cfg default/override/unknown-key. Commit `feat(recall-config): RecallConfig KV + settings registry (R1)`.

## R2: Derive taxonomy + windows from config (behavior-preserving)
In `recalls.py`, add a helper `_taxonomy(db)` that reads `cfg(db,"recall_outcomes")` and returns derived structures equivalent to the old dicts:
- `permanent = {o["label"]: o.get("reason_code") for o in outs if o["category"]=="permanent"}`
- `cooldown = {o["label"]: timedelta(days=o["cooldown_days"]) for o in outs if o["category"]=="cooldown"}`
- `completed = {o["label"] for o in outs if o["category"]=="completed"}`
- `all_labels = [o["label"] for o in outs]`
Replace the module-dict reads in `log_outcome` (validation, permanent/completed/cooldown branches) and the `/outcomes/catalog` endpoint with `_taxonomy(db)`. Replace `CLAIM_TTL` runtime use with `timedelta(minutes=cfg(db,"claim_ttl_minutes"))` in the claim flow. Replace the 24-month overdue window with `cfg(db,"overdue_window_months")`. KEEP the module-level constants as the registry-default source (settings.py builds defaults from them OR hardcodes the same — confirm they match). NO behavior change when no config row exists.
TDD `tests/test_recall_taxonomy_config.py`: (a) default `_taxonomy` equals the legacy sets (permanent keys, cooldown day-counts, completed); (b) recording "Left voicemail" sets cooldown_until ~3d out by default; (c) overriding the config (e.g. add an outcome, change a cooldown to 5d) changes behavior; (d) `/outcomes/catalog` reflects config. Suite ≤ baseline. Commit `feat(recall-config): outcome taxonomy + claim-TTL + overdue window read from settings (R2)`.

## R3: Validated GET/PUT /recalls/config
Add to `recalls.py` (prefix `/recalls`, `requires_tier(Module.RECALL, Tier.X)`): `GET /recalls/config` (WORK — recall's view tier is WORK) merged defaults+rows; `PUT /recalls/config` (MANAGE) with `RecallConfigPayload`:
- `claim_ttl_minutes: Optional[int]` ge=1 le=120
- `overdue_window_months: Optional[int]` ge=1 le=120
- `recall_outcomes: Optional[list[RecallOutcomeIn]]` where `RecallOutcomeIn(label:str, category: Literal["permanent","cooldown","completed","neutral"], cooldown_days: Optional[int] ge=0 le=365, reason_code: Optional[str])` — validate: non-empty label, category in the 4; if category=="cooldown" require cooldown_days>=1; distinct labels; at least 1 outcome. Mirror surgery's structured-config validators.
API tests (client fixture): GET defaults, PUT scalar roundtrip, PUT bad outcome (cooldown w/o days, dup labels, empty list) → 422, PUT valid taxonomy roundtrips + is reflected by /outcomes/catalog. Commit `feat(recall-config): validated GET/PUT /recalls/config (R3)`.

## R4: RecallSettings page + thin top-nav
Frontend:
- `frontend/src/pages/RecallSettings.jsx` (title "Recall Settings"), tabs: "Thresholds & Windows" (claim_ttl_minutes "Soft-Claim Lock (Minutes)", overdue_window_months "Overdue Window (Months)") bound to `/recalls/config`; "Outcomes" — an editor for the `recall_outcomes` list: rows with label, category select (permanent/cooldown/completed/neutral), cooldown_days (shown only when category=cooldown), reason_code (optional, shown for permanent), add/remove rows, Save → PUT. Surface 422 inline. Mirror SurgerySettings PostOpTab (the list-editor pattern) + LarcSettings ThresholdsTab.
- `frontend/src/components/recall/RecallNav.jsx` — thin layout nav (copy fixed PelletNav; render-time `navItems()`): Overview→/recalls (WORK, end), Settings→/recalls/settings (MANAGE). `<Outlet/>`.
- routes.jsx: nest under layout `{path:'/recalls', element:<RecallNav/>, module:M.RECALL, tier:TIER.WORK, nav:{label:'Recalls', order:50}, children:[ {index:true → Recalls}, {path:'settings', element:<RecallSettings/>, module:M.RECALL, tier:TIER.MANAGE} ]}`. Drop the old flat /recalls route; keep the parent nav for sidebar.
- Recalls.jsx: no header nav buttons to remove (it just has a title) — leave its body; the nav bar now sits above via the layout.
Build clean. Commit `feat(recall-settings): settings page (Thresholds + Outcomes tabs) + thin top-nav (R4)`.

## R5: Headless smoke + deploy
1. build + `vite preview` + Playwright load root → /login, 0 console errors (rules out circular-init crash) BEFORE deploy.
2. Merge to main, push; deploy backend then frontend; update-traffic --to-latest; smoke /api/recalls/config→401, frontend 200.
3. User authed smoke: /recalls nav + /recalls/settings both tabs, threshold save, outcome edit reflects in the call-outcome dropdown, tier-gating.

## Out of scope
- recall_type free-text stays as-is (no catalog table).
- Filter presets unchanged.
