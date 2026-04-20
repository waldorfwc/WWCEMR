# Phase 2a00 — Admin User Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an admin-only `/admin` page with a `GET/PATCH/POST` backend under `/api/admin/users` so the owner can list, edit, and pre-create users without running sqlite or editing scripts.

**Architecture:** New `admin_users` router guarded by `require_group("admin")` at the `include_router` level. Inline-edit React table with group `<select>` and display_name text input — both auto-save via React Query mutations. "Add user" button reveals an inline form. Last-admin protection enforced in the PATCH handler. All writes audited.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend); React 18 + Vite + Tailwind + React Query v5 (frontend).

**Reference spec:** `docs/superpowers/specs/2026-04-20-phase2a00-admin-user-manager-design.md`

---

## Pre-flight notes

- `require_group` and the `User` model are from Phase 2a0 — no new primitives needed on the backend.
- Existing `clinical_client` fixture exists in conftest; we add a `billing_client` fixture here so we can test that billing users also get 403.
- All three endpoints live on one new router `backend/app/routers/admin_users.py` registered with a single `Depends(auth.require_group("admin"))` at the `include_router` call (mirrors how the BILLING list is applied in `main.py`).
- React Query v5 invalidation pattern: `queryClient.invalidateQueries({ queryKey: ['admin-users'] })` after a successful PATCH/POST.
- Audit columns: `log_action(db, action, resource_type, resource_id=email, user_name=<admin>, old_values=..., new_values=..., description=...)` — `old_values`/`new_values` are dict params already supported by the service (Phase 0+).

---

## Task 1: Backend — `admin_users` router, GET endpoint

**Files:**
- Create: `backend/app/routers/admin_users.py`
- Create: `backend/tests/test_admin_users.py`
- Modify: `backend/app/main.py` (new include_router with admin guard)
- Modify: `backend/tests/conftest.py` (add `billing_client` fixture)

- [ ] **Step 1: Add `billing_client` fixture to conftest**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/conftest.py`:

```python
BILLING_USER = {"email": "biller@waldorfwomenscare.com", "name": "Biller", "group": "billing"}


@pytest.fixture
def billing_client(db):
    """Same as `client` but the authenticated user has group=billing."""
    def override_get_db():
        try:
            yield db
        finally:
            pass
    def override_get_current_user():
        return BILLING_USER

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Write failing tests for GET**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_admin_users.py`:

```python
"""Tests for /api/admin/users endpoints."""
from app.models.user import User, UserGroup


def _seed_three(db):
    db.add_all([
        User(email="a1@waldorfwomenscare.com", group=UserGroup.ADMIN, display_name="A One"),
        User(email="b1@waldorfwomenscare.com", group=UserGroup.BILLING, display_name="B One"),
        User(email="c1@waldorfwomenscare.com", group=UserGroup.CLINICAL, display_name="C One"),
    ])
    db.commit()


def test_admin_users_list_returns_sorted(client, db):
    _seed_three(db)
    r = client.get("/api/admin/users")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    # Sort: admin → billing → clinical, then email asc
    groups_in_order = [row["group"] for row in body]
    # The TEST_USER (admin) is auto-created by upsert in real flows but NOT
    # inserted by the conftest override — so body holds only the 3 seeded rows.
    assert len(body) == 3
    assert groups_in_order == ["admin", "billing", "clinical"]
    assert [row["email"] for row in body] == [
        "a1@waldorfwomenscare.com",
        "b1@waldorfwomenscare.com",
        "c1@waldorfwomenscare.com",
    ]
    assert body[0]["display_name"] == "A One"


def test_admin_users_list_forbidden_for_billing(billing_client, db):
    _seed_three(db)
    assert billing_client.get("/api/admin/users").status_code == 403


def test_admin_users_list_forbidden_for_clinical(clinical_client, db):
    _seed_three(db)
    assert clinical_client.get("/api/admin/users").status_code == 403
```

- [ ] **Step 3: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_admin_users.py -v 2>&1 | tail -15
```
Expected: all three fail with 404 (endpoint doesn't exist).

- [ ] **Step 4: Create `backend/app/routers/admin_users.py`**

```python
"""Admin user manager — admin-only CRUD on the users table."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserGroup
from app.services.audit_service import log_action
from app.routers.auth import get_current_user

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


class CreateUserPayload(BaseModel):
    email: EmailStr
    group: UserGroup
    display_name: Optional[str] = None


class UpdateUserPayload(BaseModel):
    group: Optional[UserGroup] = None
    display_name: Optional[str] = None


def _sort_key(u: User) -> tuple:
    # admin → billing → clinical, then email asc
    order = {UserGroup.ADMIN: 0, UserGroup.BILLING: 1, UserGroup.CLINICAL: 2}
    return (order.get(u.group, 99), u.email or "")


def _serialize(u: User) -> dict:
    group_val = u.group.value if hasattr(u.group, "value") else u.group
    return {
        "email": u.email,
        "group": group_val,
        "display_name": u.display_name,
        "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
        "updated_at": u.updated_at.isoformat() + "Z" if u.updated_at else None,
    }


@router.get("")
def list_users(db: Session = Depends(get_db)):
    rows = db.query(User).all()
    rows.sort(key=_sort_key)
    return [_serialize(u) for u in rows]
```

- [ ] **Step 5: Wire router into `main.py` with admin guard**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/main.py`.

Add to the router imports line (alongside the others):
```python
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch, admin_users
```

Above the existing BILLING line, add:
```python
ADMIN_ONLY = [Depends(auth.require_group("admin"))]
```

Add the new include_router call at the end of the routes block (just before the `@app.get("/api/health")` route):
```python
app.include_router(admin_users.router, prefix="/api", dependencies=ADMIN_ONLY)
```

- [ ] **Step 6: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_admin_users.py tests/ -v 2>&1 | tail -20
```
Expected: 3 new tests PASS + 58 prior tests PASS = 61 total.

- [ ] **Step 7: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/admin_users.py backend/app/main.py backend/tests/test_admin_users.py backend/tests/conftest.py
git commit -m "feat(backend): admin_users router + GET /api/admin/users (admin-only)"
```

---

## Task 2: Backend — PATCH `/api/admin/users/{email}` with last-admin guard

**Files:**
- Modify: `backend/app/routers/admin_users.py` (append PATCH)
- Modify: `backend/tests/test_admin_users.py` (append PATCH tests)

- [ ] **Step 1: Append failing tests**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_admin_users.py`:

```python
def test_admin_users_patch_group_success(client, db):
    _seed_three(db)
    r = client.patch("/api/admin/users/b1@waldorfwomenscare.com",
                     json={"group": "admin"})
    assert r.status_code == 200, r.text
    assert r.json()["group"] == "admin"

    row = db.query(User).filter(User.email == "b1@waldorfwomenscare.com").first()
    assert row.group == UserGroup.ADMIN


def test_admin_users_patch_display_name_success(client, db):
    _seed_three(db)
    r = client.patch("/api/admin/users/c1@waldorfwomenscare.com",
                     json={"display_name": "Clinician Updated"})
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Clinician Updated"


def test_admin_users_patch_cannot_remove_last_admin(client, db):
    # Only one admin
    db.add(User(email="only.admin@waldorfwomenscare.com", group=UserGroup.ADMIN))
    db.commit()
    r = client.patch("/api/admin/users/only.admin@waldorfwomenscare.com",
                     json={"group": "billing"})
    assert r.status_code == 409
    assert "last admin" in r.json()["detail"].lower()

    # Row unchanged
    row = db.query(User).filter(User.email == "only.admin@waldorfwomenscare.com").first()
    assert row.group == UserGroup.ADMIN


def test_admin_users_patch_demote_admin_when_another_exists(client, db):
    db.add_all([
        User(email="a1@waldorfwomenscare.com", group=UserGroup.ADMIN),
        User(email="a2@waldorfwomenscare.com", group=UserGroup.ADMIN),
    ])
    db.commit()
    # Two admins — demoting one is allowed
    r = client.patch("/api/admin/users/a1@waldorfwomenscare.com",
                     json={"group": "billing"})
    assert r.status_code == 200


def test_admin_users_patch_404_on_unknown_email(client, db):
    r = client.patch("/api/admin/users/nobody@waldorfwomenscare.com",
                     json={"group": "billing"})
    assert r.status_code == 404


def test_admin_users_patch_forbidden_for_billing(billing_client, db):
    _seed_three(db)
    r = billing_client.patch("/api/admin/users/b1@waldorfwomenscare.com",
                             json={"group": "admin"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_admin_users.py -v 2>&1 | tail -20
```
Expected: the 6 new PATCH tests FAIL with 404/405 (method not supported).

- [ ] **Step 3: Append PATCH endpoint to `admin_users.py`**

```python
@router.patch("/{email}")
def update_user(
    email: str,
    payload: UpdateUserPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = db.query(User).filter(User.email == email).first()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")

    old = {"group": row.group.value if hasattr(row.group, "value") else row.group,
           "display_name": row.display_name}

    # Last-admin guard
    if payload.group is not None and payload.group != UserGroup.ADMIN and row.group == UserGroup.ADMIN:
        admin_count = db.query(User).filter(User.group == UserGroup.ADMIN).count()
        if admin_count <= 1:
            raise HTTPException(status_code=409, detail="cannot remove the last admin")

    if payload.group is not None:
        row.group = payload.group
    if payload.display_name is not None:
        row.display_name = payload.display_name
    db.commit()
    db.refresh(row)

    new = {"group": row.group.value if hasattr(row.group, "value") else row.group,
           "display_name": row.display_name}
    log_action(db, "USER_UPDATED", "user",
               resource_id=email,
               user_name=current_user.get("email"),
               old_values=old, new_values=new,
               description=f"admin {current_user.get('email')} updated {email}")

    return _serialize(row)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_admin_users.py -v 2>&1 | tail -15
```
Expected: 9 tests PASS (3 list + 6 patch).

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/admin_users.py backend/tests/test_admin_users.py
git commit -m "feat(backend): PATCH /admin/users/{email} with last-admin guard + audit"
```

---

## Task 3: Backend — POST `/api/admin/users`

**Files:**
- Modify: `backend/app/routers/admin_users.py` (append POST)
- Modify: `backend/tests/test_admin_users.py` (append POST tests)

- [ ] **Step 1: Append failing tests**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_admin_users.py`:

```python
def test_admin_users_post_creates(client, db):
    r = client.post("/api/admin/users",
                    json={"email": "new@waldorfwomenscare.com",
                          "group": "billing",
                          "display_name": "New Hire"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "new@waldorfwomenscare.com"
    assert body["group"] == "billing"
    assert body["display_name"] == "New Hire"

    row = db.query(User).filter(User.email == "new@waldorfwomenscare.com").first()
    assert row is not None
    assert row.group == UserGroup.BILLING


def test_admin_users_post_lowercases_and_strips_email(client, db):
    r = client.post("/api/admin/users",
                    json={"email": "  MixedCase@waldorfwomenscare.com  ",
                          "group": "clinical"})
    assert r.status_code == 201
    assert r.json()["email"] == "mixedcase@waldorfwomenscare.com"
    row = db.query(User).filter(User.email == "mixedcase@waldorfwomenscare.com").first()
    assert row is not None


def test_admin_users_post_duplicate_email(client, db):
    db.add(User(email="dup@waldorfwomenscare.com", group=UserGroup.BILLING))
    db.commit()
    r = client.post("/api/admin/users",
                    json={"email": "dup@waldorfwomenscare.com",
                          "group": "billing"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"].lower()


def test_admin_users_post_invalid_group(client, db):
    r = client.post("/api/admin/users",
                    json={"email": "bad@waldorfwomenscare.com",
                          "group": "superuser"})
    assert r.status_code == 422


def test_admin_users_post_forbidden_for_clinical(clinical_client, db):
    r = clinical_client.post("/api/admin/users",
                             json={"email": "x@waldorfwomenscare.com",
                                   "group": "billing"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_admin_users.py -v 2>&1 | tail -15
```
Expected: 5 new POST tests FAIL with 404/405.

- [ ] **Step 3: Append POST endpoint to `admin_users.py`**

```python
@router.post("", status_code=201)
def create_user(
    payload: CreateUserPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    email = str(payload.email).lower().strip()
    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="user already exists")

    row = User(
        email=email,
        group=payload.group,
        display_name=payload.display_name,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log_action(db, "USER_CREATED_BY_ADMIN", "user",
               resource_id=email,
               user_name=current_user.get("email"),
               new_values={"group": payload.group.value, "display_name": payload.display_name},
               description=f"admin {current_user.get('email')} pre-created {email} as {payload.group.value}")
    return _serialize(row)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_admin_users.py tests/ -v 2>&1 | tail -10
```
Expected: 14 admin-user tests + 58 prior tests = 72 total PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/admin_users.py backend/tests/test_admin_users.py
git commit -m "feat(backend): POST /admin/users (pre-create user, lowercases email)"
```

---

## Task 4: Frontend — Admin.jsx page

**Files:**
- Create: `frontend/src/pages/Admin.jsx`
- Modify: `frontend/src/App.jsx` (add /admin route)

- [ ] **Step 1: Create `frontend/src/pages/Admin.jsx`**

Write EXACTLY this content:

```jsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

const GROUPS = [
  { value: 'admin',    label: 'Admin' },
  { value: 'billing',  label: 'Billing' },
  { value: 'clinical', label: 'Clinical' },
]

function Flash({ kind, text }) {
  if (!text) return null
  const cls = kind === 'ok'
    ? 'text-success'
    : 'text-danger'
  return <span className={`ml-2 text-[11px] ${cls}`}>{text}</span>
}

function UserRow({ u, onFlash, flashKind, flashText }) {
  const queryClient = useQueryClient()
  const [nameDraft, setNameDraft] = useState(u.display_name || '')

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/admin/users/${encodeURIComponent(u.email)}`, body).then(r => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      onFlash(u.email, 'ok', '✓ saved')
    },
    onError: (err) => {
      onFlash(u.email, 'err', `✗ ${err?.response?.data?.detail || 'error'}`)
    },
  })

  return (
    <tr className="table-row">
      <td className="table-td font-mono text-[11px]">{u.email}</td>
      <td className="table-td">
        <input
          className="input w-full max-w-[200px] py-1 text-[12px]"
          value={nameDraft}
          onChange={(e) => setNameDraft(e.target.value)}
          onBlur={() => {
            if ((nameDraft || '') !== (u.display_name || '')) {
              patch.mutate({ display_name: nameDraft })
            }
          }}
          placeholder="—"
        />
      </td>
      <td className="table-td">
        <select
          className="input w-[120px] py-1 text-[12px]"
          value={u.group}
          onChange={(e) => patch.mutate({ group: e.target.value })}
        >
          {GROUPS.map(g => <option key={g.value} value={g.value}>{g.label}</option>)}
        </select>
      </td>
      <td className="table-td text-[11px] text-muted">
        {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
      </td>
      <td className="table-td">
        <Flash kind={flashKind} text={flashText} />
      </td>
    </tr>
  )
}

function AddUserForm({ onClose, onFlash }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [group, setGroup] = useState('billing')
  const [displayName, setDisplayName] = useState('')

  const create = useMutation({
    mutationFn: () => api.post('/admin/users', {
      email: email.trim().toLowerCase(),
      group,
      display_name: displayName || null,
    }).then(r => r.data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      onFlash(data.email, 'ok', '✓ created')
      onClose()
    },
  })

  return (
    <tr className="bg-plum-50">
      <td className="table-td">
        <input
          className="input w-full py-1 text-[12px] font-mono"
          placeholder="email@waldorfwomenscare.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoFocus
        />
      </td>
      <td className="table-td">
        <input
          className="input w-full py-1 text-[12px]"
          placeholder="Display name (optional)"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </td>
      <td className="table-td">
        <select className="input w-[120px] py-1 text-[12px]"
                value={group} onChange={(e) => setGroup(e.target.value)}>
          {GROUPS.map(g => <option key={g.value} value={g.value}>{g.label}</option>)}
        </select>
      </td>
      <td className="table-td" />
      <td className="table-td">
        <div className="flex gap-2 items-center">
          <button className="btn-primary py-1 px-2 text-[11px]"
                  onClick={() => create.mutate()}
                  disabled={!email || create.isPending}>
            {create.isPending ? '...' : 'Create'}
          </button>
          <button className="text-[11px] text-muted underline" onClick={onClose}>Cancel</button>
          {create.isError && (
            <span className="text-[11px] text-danger">
              ✗ {create.error?.response?.data?.detail || 'error'}
            </span>
          )}
        </div>
      </td>
    </tr>
  )
}

export default function Admin() {
  const { data: users, isLoading } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => api.get('/admin/users').then(r => r.data),
  })

  const [adding, setAdding] = useState(false)
  const [flashes, setFlashes] = useState({})  // email -> {kind, text}

  function onFlash(email, kind, text) {
    setFlashes(prev => ({ ...prev, [email]: { kind, text } }))
    const timeout = kind === 'err' ? 3000 : 1500
    setTimeout(() => {
      setFlashes(prev => {
        const next = { ...prev }
        delete next[email]
        return next
      })
    }, timeout)
  }

  const counts = (users || []).reduce((acc, u) => {
    acc[u.group] = (acc[u.group] || 0) + 1
    return acc
  }, {})

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">User management</h1>
          <div className="text-muted text-[12px] mt-0.5">
            {(users?.length || 0)} users ·{' '}
            {counts.admin || 0} admin ·{' '}
            {counts.billing || 0} billing ·{' '}
            {counts.clinical || 0} clinical
          </div>
        </div>
        {!adding && (
          <button className="btn-primary" onClick={() => setAdding(true)}>
            + Add user
          </button>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Email</th>
              <th className="table-th">Display name</th>
              <th className="table-th">Group</th>
              <th className="table-th">Created</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody>
            {adding && <AddUserForm onClose={() => setAdding(false)} onFlash={onFlash} />}
            {isLoading && (
              <tr><td colSpan={5} className="table-td text-center text-muted py-8">Loading...</td></tr>
            )}
            {!isLoading && users?.map(u => (
              <UserRow key={u.email} u={u}
                       onFlash={onFlash}
                       flashKind={flashes[u.email]?.kind}
                       flashText={flashes[u.email]?.text} />
            ))}
            {!isLoading && users?.length === 0 && (
              <tr><td colSpan={5} className="table-td text-center text-muted py-8">No users yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Add the `/admin` route in App.jsx**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/App.jsx`.

Add this import near the other page imports (alphabetical near `Appeals` is fine):
```jsx
import Admin from './pages/Admin'
```

Inside `ProtectedApp`, the existing `useCurrentUser()` call returns `isClinical` and `isLoading`. You'll also need `isAdmin` — change:
```jsx
  const { isClinical, isLoading } = useCurrentUser()
```
to:
```jsx
  const { isAdmin, isClinical, isLoading } = useCurrentUser()
```

Inside `<Routes>`, add a new route (place it after `/audit`):
```jsx
            <Route path="/admin" element={
              isLoading ? null : (isAdmin ? <Admin /> : <Navigate to="/" replace />)
            } />
```

- [ ] **Step 3: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 4: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/Admin.jsx frontend/src/App.jsx
git commit -m "feat(frontend): /admin page with inline-edit user table + add-user form"
```

---

## Task 5: Frontend — Add "Admin" nav entry for admin users

**Files:**
- Modify: `frontend/src/components/layout/TopNav.jsx`

- [ ] **Step 1: Update the TopNav to conditionally include Admin**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/components/layout/TopNav.jsx`.

Find the existing `const nav = [...]` array (8 items) and `const CLINICAL_NAV = [...]` array (1 item). Right below them, add:

```jsx
const ADMIN_NAV_ENTRY = { to: '/admin', label: 'Admin' }
```

Find the existing hook call in the component body:
```jsx
  const { isClinical } = useCurrentUser()
  const visibleNav = isClinical ? CLINICAL_NAV : nav
```

Change it to:
```jsx
  const { isAdmin, isClinical } = useCurrentUser()
  const visibleNav = isClinical
    ? CLINICAL_NAV
    : (isAdmin ? [...nav, ADMIN_NAV_ENTRY] : nav)
```

No other changes.

- [ ] **Step 2: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/components/layout/TopNav.jsx
git commit -m "feat(frontend): TopNav shows 'Admin' entry for admin users"
```

---

## Task 6: Final verification

**Files:** none — verification only.

- [ ] **Step 1: Full backend suite**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ 2>&1 | tail -5
```
Expected: 72 PASS (58 prior + 14 new admin-user tests).

- [ ] **Step 2: Frontend build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 3: Manual smoke (admin)**

Start the stack (`./start.sh`). Signed in as admin:
- TopNav shows 9 entries (ends with "Admin").
- Click "Admin" → table renders with your user + any auto-created ones.
- Click the group dropdown for any non-self row → change to a different group → see `✓ saved` flash.
- Change a display name → blur → see `✓ saved` flash.
- Click "+ Add user" → inline form appears → enter an email like `fake@waldorfwomenscare.com`, group `billing`, name "Test" → Create → row appears in the table.
- Try to demote yourself (the only admin) — see red `✗ cannot remove the last admin` flash; your row stays admin.
- Direct-navigate to `/admin` as a billing or clinical user (flip via sqlite) — should redirect to `/`.

- [ ] **Step 4: Final empty commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git commit --allow-empty -m "test: 2a00 Admin user manager verified end-to-end"
```

---

## Self-review

- **Spec coverage:** ✓
  - GET/PATCH/POST endpoints → T1, T2, T3
  - Admin-only guard at `include_router` level → T1
  - 403 for billing + clinical → T1, T2, T3
  - Last-admin guard → T2
  - Email lowercasing + strip → T3
  - USER_UPDATED + USER_CREATED_BY_ADMIN audit entries → T2, T3
  - Admin page with inline group select + display-name input → T4
  - "Add user" inline form → T4
  - Non-admins redirect → T4
  - TopNav Admin entry visible only for admin → T5
  - Tests: 14 backend tests total (3 list + 6 patch + 5 post) — matches spec's 10 and adds 4 more edge cases.

- **Placeholder scan:** ✓ No TBD/TODO/"handle appropriately". Each task's steps include full code/commands.

- **Type consistency:** ✓
  - `CreateUserPayload(email, group, display_name)` and `UpdateUserPayload(group?, display_name?)` consistent with backend usage.
  - Frontend page uses `email`, `group`, `display_name`, `created_at` keys matching `_serialize()`.
  - `canSeeBilling`/`isAdmin` flags match the `useCurrentUser` hook shape from 2a0.
