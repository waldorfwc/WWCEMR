# Phase 2a0 — Groups & Page Visibility

**Date:** 2026-04-20
**Project:** wwc-era-project
**Depends on:** Phase 1 (auth + `get_current_user` already exist)
**Blocks:** Phase 2a (claim & service-line editing)

## Goal

Introduce a minimal groups-and-visibility layer so clinical staff see only Charts and the patient chart itself, while billing staff and admin see the full app. Small, deliberately-scoped precursor to a real role/permissions system (deferred to later 2a00).

## Workflow

1. User signs in via Google OAuth (unchanged).
2. Backend looks up the user's email in the new `users` table.
3. Existing user → returns their group. New user → creates a row with `group='clinical'`, logs an audit entry.
4. Every API call enforces group-based access. Clinical users hit 403 on billing endpoints.
5. Frontend shows/hides nav entries and in-page affordances based on group. Clinical users land on `/documents` after login.

## Non-goals

- Custom group creation / naming (a later 2a00 feature).
- Per-feature access levels.
- Per-field edit permissions.
- UI to manage group membership — membership is changed via seed script or direct DB update in this phase.
- Role hierarchy / inheritance.

## Data model

New `User` model in `backend/app/models/user.py`:

```
email         : str (80) PK, lowercase-normalized
group         : SAEnum(UserGroup) default UserGroup.CLINICAL, indexed
display_name  : str (200), nullable
created_at    : datetime
updated_at    : datetime
```

Enum:
```python
class UserGroup(str, enum.Enum):
    ADMIN = "admin"
    BILLING = "billing"
    CLINICAL = "clinical"
```

No other schema changes.

## Auth flow

`get_current_user` (in `backend/app/routers/auth.py`) currently returns a dict from the verified JWT. Extend it to:

1. Validate the JWT (unchanged).
2. Normalize email to lowercase.
3. Upsert a `User` row:
   - If exists, read `group` and `display_name`.
   - If not, insert with `group=CLINICAL`, log an `audit_logs` entry `USER_CREATED` (`user_name`=email, `description`="auto-created with default group clinical").
4. Return a dict: `{email, name, picture, group}` — existing fields plus `group`.

The conftest `override_get_current_user` returns the existing `TEST_USER` dict extended with `group="admin"` so existing tests keep passing.

## Backend — route guard

New helper in `backend/app/routers/auth.py`:

```python
def require_group(*allowed: str):
    """FastAPI dependency factory: 403 if current user's group isn't in allowed."""
    def _dep(current_user: dict = Depends(get_current_user)):
        if current_user.get("group") not in allowed:
            raise HTTPException(status_code=403, detail="forbidden")
        return current_user
    return _dep
```

Applied as a router-level dependency on every billing-only router. In `backend/app/main.py`:

```python
BILLING = [Depends(auth.require_group("admin", "billing"))]

app.include_router(claims.router,   prefix="/api", dependencies=BILLING)
app.include_router(denials.router,  prefix="/api", dependencies=BILLING)
app.include_router(appeals.router,  prefix="/api", dependencies=BILLING)
app.include_router(eob.router,      prefix="/api", dependencies=BILLING)
app.include_router(audit.router,    prefix="/api", dependencies=BILLING)
app.include_router(waystar.router,  prefix="/api", dependencies=BILLING)
app.include_router(ar.router,       prefix="/api", dependencies=BILLING)
app.include_router(imports.router,  prefix="/api", dependencies=BILLING)
app.include_router(dashboard.router, prefix="/api", dependencies=BILLING)
app.include_router(fax.router,       prefix="/api", dependencies=BILLING)
app.include_router(fax_batch.router, prefix="/api", dependencies=BILLING)
app.include_router(fax_batch.log_router, prefix="/api", dependencies=BILLING)
```

Open to clinical (no group guard):
- `chart`, `documents`, `patients`, `intake`, `auth`, `health`

`intake` is clinical-facing (patient intake forms). `documents` handles the chart-browsing endpoints. Both stay reachable.

## Seed script

`backend/scripts/seed_users.py` — idempotent. Hard-coded list of emails and groups. Runs `upsert` logic: if user exists, updates group; if not, inserts. Emits `[add]` / `[update]` / `[skip]` per row.

Initial list contains only the owner; the human edits the list to add coworkers and re-runs the script:
```python
USERS = [
    ("ocooke@waldorfwomenscare.com", "admin", "Owner"),
]
```
Script is idempotent so safe to re-run after edits.

## Frontend

### `useCurrentUser` hook
New `frontend/src/hooks/useCurrentUser.js`. Wraps the existing `user` state (already stored in `localStorage` per `App.jsx`) plus fetches `/api/auth/me` (new endpoint returning the full user dict with group). Exposes:

```js
{ email, name, picture, group,
  isAdmin, isBilling, isClinical,
  canSeeBilling  // true if admin or billing
}
```

### New endpoint `GET /api/auth/me`
Returns the current user dict including group. Auth-guarded (no group restriction). Used by the frontend hook on mount.

### TopNav
`nav` array filtered by group. Current array has 8 entries after the Charts-page work; clinical users see only `Dashboard`-free set:

```js
// Clinical: [Charts only]
// Billing / Admin: current full nav
```

Actually — per confirmed design: clinical sees **only Charts**. No Dashboard, no A/R, no Claims, etc. The "Charts" label stays but the route is `/documents`.

### Default landing route

`App.jsx` currently redirects `/` to `Dashboard`. After sign-in:
- If `group === 'clinical'`: redirect to `/documents`
- Otherwise: render Dashboard at `/` as today

Also: clinical users who navigate manually to `/` (Dashboard) get redirected to `/documents` client-side.

### Conditional affordances

**`Documents.jsx`:**
- For clinical users, hide the `<FaxLogPane />` right pane. Patient list expands to full width (change the grid template columns from `'280px 1fr'` to `'1fr'`).

**`PatientChart.jsx`:**
- For clinical users, hide the batch-fax action bar ("Fax N docs to EMA →", "Select unsent", "Clear").
- Hide the `FaxStatusChip` per-row (billing signal, not useful to clinical).
- Per-doc "View" and "Download" buttons stay (clinical needs to see documents).
- The `useFaxByChart` hook is conditional on `useCurrentUser().canSeeBilling` — clinical users skip the call entirely so they don't hit the `/api/fax/by-chart/*` 403.
- Everything else on the chart (demographics, PMH, meds, etc.) stays as-is.

### Error handling
- 403 responses from billing endpoints get a generic toast "You don't have access to this feature." No redirect loop.

## Files touched

**Backend — created:**
- `backend/app/models/user.py`
- `backend/scripts/seed_users.py`
- `backend/tests/test_user_groups.py`

**Backend — modified:**
- `backend/app/database.py` (line 23) — add `user` to the models import
- `backend/app/routers/auth.py` — extend `get_current_user` with users-table lookup; add `require_group`; add `GET /me`
- `backend/app/main.py` — attach `require_group` dependency to billing routers
- `backend/tests/conftest.py` — extend `TEST_USER` with `group='admin'`

**Frontend — created:**
- `frontend/src/hooks/useCurrentUser.js`

**Frontend — modified:**
- `frontend/src/components/layout/TopNav.jsx` — filter nav by group
- `frontend/src/App.jsx` — post-login redirect + clinical redirect from `/`
- `frontend/src/pages/Documents.jsx` — hide fax-log pane for clinical
- `frontend/src/pages/PatientChart.jsx` — hide batch-fax affordances for clinical

## Verification

- `pytest backend/tests/` — all prior tests still PASS; new `test_user_groups.py` passes.
- Manual smoke with a clinical-group test user: login → lands on `/documents`; nav shows only Charts; `/api/claims` returns 403 (browser devtools); `PatientChart` doesn't show batch-fax UI.
- Manual smoke with billing user: everything works as before; nav unchanged.
- Admin smoke: same as billing — no difference yet.

## Open questions

None blocking.
