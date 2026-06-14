# Surgery Intake — auto-pull + manual consent selection (attach only)

> subagent-driven-development. Backend TDD; suite baseline 69 failed / 0 errors. Frontend build + headless load before deploy.

**Branch:** `feat/intake-consents` off `main`.

**Goal:** In Add New Surgery / Update Surgery, show a Consents section that auto-pulls matched consent templates (from procedure/facility/insurance) and lets staff add/remove them; the curated selection is stored on the surgery. Sending stays a separate explicit action (existing "Send via BoldSign"). On edit, re-pull refreshes matches but preserves manual add/remove.

## Decisions
- **Attach only** — selection is recorded; NOT auto-sent.
- **Re-pull keeps manual edits** — track overrides {added, removed}; effective = (matched ∪ added) − removed.
- Stored selection is authoritative for sending (falls back to matcher if none stored).

## Current state (verified)
- Matcher: `backend/app/services/consent_template_matcher.py` `match_templates_for_surgery(db, surgery, today=None) -> list[TemplateMatch]` (reads surgery.procedures, selected_facility, primary_insurance, scheduled_date; queries ConsentTemplate). `TemplateMatch` has template + is_supplemental + warnings (confirm fields).
- Preview endpoint exists: `GET /surgery/{id}/consent/template-matches` → {matches, unmatched_procedures}.
- `POST /consent-templates/test-match` (per procedure/cpt/facility/insurance) exists. `GET /consent-templates` (Surgery MANAGE) lists all.
- Envelopes (`SurgeryConsentEnvelope`) created only on `POST /surgery/{id}/consent/boldsign-send` via `send_consent_envelopes()` (boldsign_envelopes.py ~320). No stored "selected templates" today.
- `create_manual`/`patch_surgery` do nothing with consents. `_surgery_dict` returns no consent template selection.

---

## B1 — Storage + persistence (backend)
**Files:** `backend/app/models/surgery.py`, `backend/app/database.py`, `backend/app/routers/surgery.py`, test.
1. Surgery model: add
   ```python
   consent_template_ids = Column(JSON, nullable=True)   # list[str] selected template IDs (curated)
   consent_overrides    = Column(JSON, nullable=True)   # {"added": [...], "removed": [...]} manual deltas
   ```
2. `database.py` `_apply_lightweight_migrations()` `needed`: add `("surgeries","consent_template_ids","JSON")`, `("surgeries","consent_overrides","JSON")`.
3. `_surgery_dict`: return `consent_template_ids` (list, default []), `consent_overrides` (dict, default {"added":[],"removed":[]}), and `consent_templates_selected`: a resolved list `[{"id","name","is_supplemental"}]` for the stored IDs (query ConsentTemplate by id; skip missing). Place near existing consent_* keys.
4. `ManualSurgeryIn`: add `consent_template_ids: list[str] = []`, `consent_overrides: Optional[dict] = None`. `create_manual`: persist both (store ids as-is; overrides default {"added":[],"removed":[]}); if `consent_template_ids` non-empty and `clearance_status`-style: set `consent_required = True` and, if `consent_status` is the not-required default, set `consent_status = "required"` (grep valid consent_status values first; use the existing not-yet-sent value).
5. `SurgeryPatch`: add `consent_template_ids: Optional[list[str]]`, `consent_overrides: Optional[dict]`. `patch_surgery`: when provided, set them explicitly (pop from the generic loop). Re-arm `consent_required`/`consent_status` like create (only escalate not_required→required; never downgrade sent/signed).
6. Test `backend/tests/test_intake_consents.py`: POST /surgery/manual with consent_template_ids=[<id>] → GET returns the ids + resolved `consent_templates_selected` + consent_required True; PATCH updates the list; empty list leaves consent_status untouched if already sent/signed.
Commit `feat(surgery): store curated consent_template_ids + overrides on surgery (B1)`.

---

## B2 — Match-preview + template-picker endpoints (backend)
**File:** `backend/app/routers/surgery.py` (or consent_templates.py), test.
1. `POST /surgery/consent/match-preview` (gate `requires_tier(Module.SURGERY, Tier.WORK)`): body `{procedures: list[{cpt?,description?}], eligible_facilities?: list[str], selected_facility?: str, primary_insurance?: str, scheduled_date?: str}`. Build a TRANSIENT `Surgery(...)` (NOT added to the session) with these attrs (selected_facility: use provided, else the single eligible facility if exactly one, else None; scheduled_date parsed if provided). Call `match_templates_for_surgery(db, transient)`. **First verify the matcher reads only plain attributes** (procedures, selected_facility, primary_insurance, scheduled_date) and not surgery.id/relationships — if it touches anything requiring persistence, pass a lightweight stand-in object exposing those attrs instead. Return `{"matches": [{"template_id","name","is_supplemental","warnings": [...]}], "unmatched_procedures": [...]}`.
2. `GET /surgery/consent/templates` (gate WORK): return active ConsentTemplate rows as `[{"id","name","is_supplemental"}]` (ordered is_supplemental, name) — for the manual-add picker (avoids the MANAGE gate on `/consent-templates`).
3. Test: match-preview with a payload that matches a seeded template returns it (seed/insert a ConsentTemplate in the test); match-preview with no procedures returns empty matches; GET templates returns active ones only.
Commit `feat(surgery): consent match-preview + active-template picker endpoints for intake (B2)`.

---

## B3 — Sending respects stored selection (backend)
**Files:** `backend/app/services/boldsign_envelopes.py`, `backend/app/routers/surgery.py`, test.
1. `send_consent_envelopes(db, surgery, ...)`: if `surgery.consent_template_ids` is a non-empty list, send THOSE templates (query ConsentTemplate by id, skip inactive/missing) instead of calling the matcher. If empty/None, keep current matcher behavior (back-compat). Preserve all existing envelope-dedup/skip-already-sent and warning logic (warnings: if using stored selection, still run the min-days warning check per template via the matcher's warning helper, or skip warnings — match existing structure; keep it simple: reuse existing warning computation if easily callable, else send without the min-days gate but log). Keep idempotency (don't resend signed/sent).
2. `GET /surgery/{id}/consent/template-matches`: when `surgery.consent_template_ids` is non-empty, return the stored selection resolved to `{template_id,name,is_supplemental}` as `matches` (so the detail page "will send" preview reflects the curated list); else current matcher output. Keep `unmatched_procedures` from the matcher either way (informational).
3. Test: a surgery with consent_template_ids=[A] (A active) → template-matches returns A; send creates an envelope for A only (mock the BoldSign API call as existing send tests do — grep tests for boldsign send mocking).
Commit `feat(surgery-consent): stored selection drives send + preview (B3)`.

---

## F1 — Intake Consents section (frontend)
**File:** `frontend/src/components/surgery/SurgeryIntakeForm.jsx`.
Add form state: `consent_template_ids: []`, `consent_overrides: {added: [], removed: []}`.
1. **Active template list:** `useQuery(['consent-template-picker'], () => api.get('/surgery/consent/templates').then(r=>r.data))` for the add-picker + name resolution.
2. **Auto-pull:** a debounced effect keyed on `procedures` (cpt+description), `eligible_facilities`, `primary_insurance`: when there's ≥1 procedure with a description/cpt, POST to `/surgery/consent/match-preview` ({procedures, eligible_facilities, primary_insurance}). On result, compute `matchedIds`; set `consent_template_ids = unique((matchedIds ∪ overrides.added) − overrides.removed)`. (Don't run if no procedures.) Guard against clobbering during initial prefill (see F2).
3. **Render a "Consents" section:** list selected consents (resolve id→name via picker data; show supplemental badge + any warning text from the last match result); each with a remove (×) → moves id to `overrides.removed`, out of `overrides.added`, and drops from selection. An "Add consent ▾" picker listing active templates not already selected → adds id to selection + `overrides.added`, removes from `overrides.removed`. Show match warnings (e.g. min-days) inline, non-blocking.
4. Include `consent_template_ids` + `consent_overrides` in `buildFields()` payload.
Build clean. Commit `feat(surgery-intake): Consents section — auto-pull matched + manual add/remove (F1)`.

---

## F2 — Prefill consents on Update + detail reflects selection (frontend)
**Files:** `frontend/src/components/surgery/surgeryDrawers.jsx`, (SurgeryDetail.jsx only if needed).
1. `UpdateSurgeryDrawer` `mapDetailToForm`: set `consent_template_ids` from detail (`d.consent_template_ids || []`) and `consent_overrides` from `d.consent_overrides || {added:[],removed:[]}`. Ensure the auto-pull effect doesn't wipe the prefilled selection on first load — gate the effect so it only runs after the user changes procedure/facility/insurance (e.g. skip the first run, or only re-pull when those inputs differ from the prefilled values). The create drawer starts empty so auto-pull populates normally.
2. SurgeryDetail: the existing `template-matches` query now returns the stored selection (B3) — verify the "will send" preview shows the curated list. Likely no change needed; if it reads a different shape, adjust to render `matches[].name`.
Build clean. Commit `feat(surgery-intake): prefill consents on Update + detail reflects curated selection (F2)`.

---

## F3 — Headless smoke + deploy
1. build + vite preview + Playwright load `/surgery`, `/surgery/settings` → /login, 0 console errors.
2. Merge to main; deploy backend then frontend; smoke (`/api/surgery/config` 401, `/surgery` 200, backend health 200).
3. Authed check: Add New Surgery → enter a hysterectomy/tubal procedure → consents auto-populate; add/remove one; save; open the surgery → consents reflected; "Send via BoldSign" sends exactly the curated set. Update Surgery → consents prefill; change procedure → re-pull keeps manual edits.

## Out of scope
- No auto-send at intake (explicit send unchanged).
- No new consent-template authoring (uses existing ConsentTemplate list).
- Sterilization (HHS-687) supplemental logic already handled by the matcher — unchanged.
