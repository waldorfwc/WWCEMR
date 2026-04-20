# Phase 2a00 — Admin User Manager

**Date:** 2026-04-20
**Project:** wwc-era-project
**Depends on:** Phase 2a0 (User model + groups + `require_group` guard)
**Blocks:** Phase 2a (claim & service-line editing)

## Goal

Give admins a small in-app page to list all users, change any user's group inline, and pre-create users before they sign in — eliminating the need to edit the seed script or run sqlite commands for day-to-day user management.

## Workflow

1. Admin signs in. TopNav now includes an "Admin" entry (visible only to admins).
2. Clicks "Admin" → lands on `/admin`.
3. Sees a table of every user with their group, display name, created date. Rows sorted admin → billing → clinical, then alphabetical by email.
4. Changes a user's group from a `<select>` dropdown in the row. Saves immediately; a brief `✓ saved` flash appears next to the row.
5. Clicks "Add user" in the top right → inline form expands with email, group, display_name inputs + Create button.
6. Submits — new row flashes into the table, form collapses.
7. Edge cases: demoting the last admin is blocked (409 from backend, shown as red error flash).

## Non-goals

- Delete / deactivate users (this phase doesn't remove rows; later 2a000).
- Last-seen / last-login timestamps (deferred).
- Audit-log viewer inside this page (existing `/audit` works).
- Bulk CSV import.
- Role hierarchy, custom groups, per-feature permissions (still future 2a00-style work).

## Backend

New router: `backend/app/routers/admin_users.py`. Registered in `backend/app/main.py` with `dependencies=[Depends(auth.require_group("admin"))]`:

```python
ADMIN_ONLY = [Depends(auth.require_group("admin"))]
app.include_router(admin_users.router, prefix="/api", dependencies=ADMIN_ONLY)
```

Router prefix: `/admin/users`.

### Endpoints

**`GET /api/admin/users`**

Returns a list sorted admin → billing → clinical, then email asc:
```json
[
  {
    "email": "ocooke@waldorfwomenscare.com",
    "group": "admin",
    "display_name": "Owner",
    "created_at": "2026-04-20T12:00:00Z",
    "updated_at": "2026-04-20T12:00:00Z"
  }
]
```

**`PATCH /api/admin/users/{email}`**

Body (both optional, only provided fields update):
```json
{ "group": "billing", "display_name": "Jane Smith" }
```

- 404 if email not found.
- 409 `detail: "cannot remove the last admin"` if the change would leave zero admins (computed: if old `group == "admin"` and new `group != "admin"`, count users with `group == "admin"`; reject if that count is 1).
- Writes an `AuditLog` entry `USER_UPDATED` with `resource_id=email`, `user_name=<admin doing the change>`, `old_values={group, display_name}`, `new_values={group, display_name}`.
- Returns the updated user row in the same shape as GET.

**`POST /api/admin/users`**

Body (Pydantic model `CreateUserPayload`):
```json
{ "email": "newhire@waldorfwomenscare.com", "group": "billing", "display_name": "New Hire" }
```

- `email` required, lowercased + stripped before insert.
- `group` required, must be one of `admin`/`billing`/`clinical` (422 from Pydantic enum validation otherwise).
- `display_name` optional.
- 409 `detail: "user already exists"` if email is already in the table.
- No domain validation — that's already handled at sign-in time by `ALLOWED_DOMAINS`. Pre-creating a user with an email outside those domains is allowed (could be added to domains later), but they can't actually sign in until the domain is allowed.
- Writes `USER_CREATED_BY_ADMIN` audit entry (distinct from the `USER_CREATED` written when `get_current_user` auto-creates on first sign-in).
- Returns 201 with the new user row.

### Model changes

None. The `User` model from 2a0 has every field we need (`email`, `group`, `display_name`, `created_at`, `updated_at`).

## Frontend

### New page: `frontend/src/pages/Admin.jsx`

Route wiring in `App.jsx`:
```jsx
<Route path="/admin" element={
  isLoading ? null : (isAdmin ? <AdminPage /> : <Navigate to="/" replace />)
} />
```
Non-admins get redirected to `/` (which itself redirects clinical users to `/documents`).

Page structure:
- Header: "User management" + totals line ("`N` users · `A` admin · `B` billing · `C` clinical").
- "Add user" button in top-right.
- Table with columns: Email, Display name, Group, Created. Sorted server-side.
- Each row: display_name is an inline text input (click-to-edit, blur-to-save). Group is a `<select>` with the 3 options. Both PATCH on change.
- Inline flash: a small span to the right of the row showing `✓ saved` (green, 1.5s) or `✗ <error>` (red, 3s).
- "Add user" expands a form row above the table with email input, group select, display name input, Create button. Esc or Cancel collapses. Create flashes the new row in and refetches the list.

### TopNav change

Add a conditionally-rendered entry:
```js
const ADMIN_NAV_ENTRY = { to: '/admin', label: 'Admin' }
// inside component:
const { isAdmin, isClinical } = useCurrentUser()
const visibleNav = isClinical
  ? CLINICAL_NAV
  : (isAdmin ? [...nav, ADMIN_NAV_ENTRY] : nav)
```

### React Query

- `useQuery(['admin-users'], () => api.get('/admin/users').then(r => r.data))`
- `useMutation` for PATCH — on success, invalidate `['admin-users']`; on error, surface `err.response.data.detail`.
- `useMutation` for POST — same pattern.

## Files touched

**Backend — created:**
- `backend/app/routers/admin_users.py`
- `backend/tests/test_admin_users.py`

**Backend — modified:**
- `backend/app/main.py` — include `admin_users.router` with `ADMIN_ONLY` guard

**Frontend — created:**
- `frontend/src/pages/Admin.jsx`

**Frontend — modified:**
- `frontend/src/App.jsx` — add `/admin` route
- `frontend/src/components/layout/TopNav.jsx` — conditional Admin nav entry

## Verification

- `pytest backend/tests/` — all prior tests + 7 new admin tests pass.
- Manual: open `/admin` as admin → see users; change someone's group → `✓ saved` flash; "Add user" → create a fake user → appears in table.
- Manual as non-admin (flip your group to billing via sqlite): TopNav has no "Admin" entry; direct nav to `/admin` redirects to `/`.
- Manual: try demoting yourself from admin to billing → red error flash "cannot remove the last admin".

## Tests (backend)

1. `test_admin_users_list_returns_sorted` — seed 3 users, hit GET, assert order admin → billing → clinical then email-asc
2. `test_admin_users_list_forbidden_for_billing` — 403 via `clinical_client` fixture (and by extension billing — new fixture added if needed)
3. `test_admin_users_list_forbidden_for_clinical` — 403
4. `test_admin_users_patch_group_success`
5. `test_admin_users_patch_display_name_success`
6. `test_admin_users_patch_cannot_remove_last_admin` — 409
7. `test_admin_users_patch_404_on_unknown_email`
8. `test_admin_users_post_creates_and_lowercases_email` — 201 + row in DB
9. `test_admin_users_post_duplicate_email` — 409
10. `test_admin_users_post_invalid_group` — 422

## Open questions

None blocking.
