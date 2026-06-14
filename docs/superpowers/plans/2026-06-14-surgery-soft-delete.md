# Surgery soft-delete — remove a patient/surgery from the system (recoverable)

> subagent-driven-development. Backend TDD; suite baseline 69 failed / 0 errors — the FULL SUITE PASSING AT BASELINE is the key guard that the global filter doesn't over-hide normal surgeries. Frontend build + headless load before deploy.

**Branch:** `feat/surgery-soft-delete` off `main`.

**Goal:** Soft-delete a surgery (keep the row, hide it everywhere) from the Update Surgery drawer. Recoverable via a restore endpoint. Deleted surgeries must disappear from list, search, dashboard, calendar, waitlist, capacity, sweeps, duplicate-detection, and the detail page (404).

## Current state (verified)
- `Surgery` (backend/app/models/surgery.py) has NO deleted_at/deleted_by. Other models use `SoftDeleteMixin` (deleted_at, deleted_by, `.not_deleted()`, `.soft_delete(by)`, `.restore()`).
- There IS a `POST /surgery/{id}/cancel` (Tier.WORK) that sets status='cancelled', frees the booked SurgerySlot, clears scheduled fields, bumps portal_token_version, voids BoldSign envelopes, deletes the GCal event. Cancel ≠ delete (cancelled rows stay visible).
- ~113 `db.query(Surgery)` sites across routers/services — too many to edit individually.
- Update drawer: `frontend/src/components/surgery/surgeryDrawers.jsx` `UpdateSurgeryDrawer` — has `selectedId` + loaded `detail`; invalidates `surgery-list`/`surgery-dashboard` on save.
- `_apply_lightweight_migrations()` in database.py is where column adds go.

---

## B1 — Surgery soft-delete columns
**Files:** `backend/app/models/surgery.py`, `backend/app/database.py`.
- Make `Surgery` use the existing `SoftDeleteMixin` (same as Claim/BillingDocument): `class Surgery(Base, SoftDeleteMixin)`. Confirm the mixin adds `deleted_at`/`deleted_by` and the helper methods; if mixing it in conflicts with existing columns, instead add the two columns + a `not_deleted()` classmethod manually mirroring the mixin.
- `database.py` `needed`: add `("surgeries","deleted_at","DATETIME")`, `("surgeries","deleted_by","VARCHAR(200)")`.
- Verify `import app.main` ok.
Commit `feat(surgery): add soft-delete columns to Surgery (B1)`.

---

## B2 — Global soft-delete query filter (the core)
**File:** `backend/app/database.py` (or a small `app/soft_delete.py` imported at startup).
Register a SQLAlchemy `do_orm_execute` event that injects `with_loader_criteria(Surgery, lambda cls: cls.deleted_at.is_(None), include_aliases=True)` into every ORM SELECT, UNLESS the statement carries `execution_options(include_deleted=True)`:
```python
from sqlalchemy import event
from sqlalchemy.orm import with_loader_criteria, Session as _Session

@event.listens_for(_Session, "do_orm_execute")
def _filter_soft_deleted_surgery(execute_state):
    if (execute_state.is_select
            and not execute_state.is_column_load
            and not execute_state.is_relationship_load
            and not execute_state.execution_options.get("include_deleted", False)):
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(Surgery, lambda cls: cls.deleted_at.is_(None),
                                 include_aliases=True))
```
- **VERIFY it filters the LEGACY `db.query(Surgery)` API** in this SQLAlchemy version (write a test: insert a soft-deleted surgery, `db.query(Surgery).all()` excludes it). If `do_orm_execute` does NOT fire for the legacy Query API here (older SQLAlchemy), FALL BACK to explicit `.filter(Surgery.deleted_at.is_(None))` at these user-facing sites and report the fallback: surgery.py list (~729), dashboard (~325,~331), calendar (~650), get_surgery (~1479, return 404 when deleted), waitlist endpoints; services auto_unresponsive.py (~76), reminders.py (~75), escalations.py (~71), blackout_conflict.py (~34), block_schedule.py can_fit/available_slots, candidate_import.py dup-check (~371,~406). (Prefer the global event; only fall back if it provably doesn't work.)
- Ensure the delete/restore endpoints can still load deleted rows via `db.query(...).execution_options(include_deleted=True)`.
Test `backend/tests/test_surgery_soft_delete.py`: a soft-deleted surgery is excluded from `db.query(Surgery)`; one with `include_deleted=True` is included.
Commit `feat(surgery): global soft-delete query filter for Surgery (B2)`.

---

## B3 — Delete + restore endpoints
**File:** `backend/app/routers/surgery.py`.
- `POST /surgery/{id}/delete` (gate `requires_tier(Module.SURGERY, Tier.MANAGE)`): load the surgery (normal query — it's not deleted yet); 404 if missing. If it has a booked slot/scheduled_date, free it the SAME way `cancel_surgery` does (reuse/inline the slot-free: delete the SurgerySlot row(s) for this surgery, clear `scheduled_date`/`scheduled_start_time`) so the slot reopens. Bump `portal_token_version` (invalidate patient JWTs). Then `surgery.soft_delete(current_user.get("email"))` (sets deleted_at/deleted_by). Commit. Return `{"ok": True}`. (Keep it focused — do NOT need a cancellation reason; optionally best-effort void BoldSign envelopes + GCal like cancel, but only if trivial to reuse; otherwise skip to limit scope and note it.)
- `POST /surgery/{id}/restore` (gate Tier.MANAGE): load with `include_deleted=True`; if not found/Not deleted → 404/no-op; `surgery.restore()`; commit. Return `{"ok": True}`. (No frontend yet — recovery path via API.)
Tests (extend test_surgery_soft_delete.py, client=super-admin): create surgery → `POST /surgery/{id}/delete` → `GET /surgery` list excludes it, `GET /surgery/{id}` → 404, dashboard completed/active counts exclude it; `POST /surgery/{id}/restore` → reappears in list + GET 200. Also assert a NON-deleted surgery still appears in list + GET 200 (guard against over-hiding).
Suite ≤ baseline. Commit `feat(surgery): soft-delete + restore endpoints (B3)`.

---

## F1 — Delete button in Update Surgery drawer
**File:** `frontend/src/components/surgery/surgeryDrawers.jsx` `UpdateSurgeryDrawer`.
- Once a surgery is selected (`selectedId` + `detail` loaded), show a red **"Delete patient"** button (e.g. in the drawer header or footer near Save). Gate it on MANAGE: `const { tier } = useCurrentUser(); ... {tier(MODULE.SURGERY, TIER.MANAGE) && <button>...}` (import `useCurrentUser` + `MODULE`/`TIER` from routes.jsx — these are fine in a drawer, no circular-init issue since it's not the nav layout; but build nav-free, just import the constants).
- On click → a confirm step (use the app's `useConfirm`/ConfirmDialog if available in this file's siblings, else a window.confirm) with text like "Soft-delete this surgery for {patient_name}? It will be removed from the surgery system (recoverable by an admin)." On confirm → `api.post('/surgery/${selectedId}/delete')`; on success invalidate `['surgery-list']`, `['surgery-dashboard']`, `['surgery-block-days']`, close the drawer, and surface a brief success (toast/alert consistent with the file). Surface errors inline.
Build clean. Commit `feat(surgery-intake): Delete (soft-delete) button in Update Surgery drawer (F1)`.

---

## F2 — Headless smoke + deploy
1. build + vite preview + Playwright load `/surgery` → /login, 0 console errors.
2. Merge to main; deploy backend then frontend; smoke (`/api/surgery/config` 401, `/surgery` 200, backend health 200); push origin.
3. Authed check: Update Surgery → select a patient → Delete → confirm → patient disappears from the list/dashboard/calendar; verify a non-deleted patient is unaffected; (admin) restore via API brings it back.

## Out of scope
- No "deleted surgeries" admin UI / restore button (API-only recovery this round).
- Cancel workflow unchanged (distinct from delete).
- Hard delete is never offered.
