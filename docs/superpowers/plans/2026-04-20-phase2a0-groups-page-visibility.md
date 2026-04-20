# Phase 2a0 — Groups & Page Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a `User` table with a `group` enum (admin/billing/clinical), make `get_current_user` upsert on first login (default `clinical`), guard billing-only routers with `require_group`, and hide billing affordances from clinical users on the frontend.

**Architecture:** New `users` table keyed by lowercase email. `get_current_user` performs a DB upsert on every request and returns a dict that now includes `group`. `require_group(*allowed)` is a FastAPI dependency-factory applied at `include_router` level to every billing router. Frontend adds a `useCurrentUser` hook that reads `/api/auth/me` (extended to include `group`), and filters the TopNav + hides in-page affordances based on group. Clinical users' default landing route becomes `/documents`.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite, pytest; React 18 + Vite + Tailwind + React Query v5.

**Reference spec:** `docs/superpowers/specs/2026-04-20-phase2a0-groups-page-visibility-design.md`

---

## Pre-flight notes

- `/api/auth/me` **already exists** in `backend/app/routers/auth.py:131-138` and returns `{email, name, picture}`. We're extending it (adding `group`) — not creating it. The spec's "new endpoint" line is actually "extend existing endpoint."
- `get_current_user` currently returns the raw JWT payload with no DB lookup (`backend/app/routers/auth.py:40-51`). We'll add a `db: Session = Depends(get_db)` param and upsert a `User` row per request. FastAPI's Depends resolution handles this transparently for existing callers.
- The conftest override `override_get_current_user` (`backend/tests/conftest.py`) returns a bare dict from `TEST_USER`. We add `group="admin"` to `TEST_USER` so existing tests keep working; no other conftest changes needed.
- Existing routers that already declare `Depends(get_current_user)` (fax_batch.py, fax.py) keep working — they get the extended dict without any change.
- `app.database.init_db()` hardcodes the models import on line 23; we must add `user` there so `Base.metadata.create_all()` creates the new table.

---

## Task 1: `User` model + registration

**Files:**
- Create: `backend/app/models/user.py`
- Create: `backend/tests/test_user_model.py`
- Modify: `backend/app/database.py` (line 23)
- Modify: `backend/tests/conftest.py` (extend TEST_USER)

- [ ] **Step 1: Write the failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_user_model.py`:

```python
"""Tests for User model + UserGroup enum."""
from app.models.user import User, UserGroup


def test_user_group_values():
    assert {g.value for g in UserGroup} == {"admin", "billing", "clinical"}


def test_user_defaults_to_clinical(db):
    u = User(email="new@waldorfwomenscare.com")
    db.add(u)
    db.commit()
    db.refresh(u)
    assert u.group == UserGroup.CLINICAL
    assert u.created_at is not None
    assert u.display_name is None


def test_user_email_is_primary_key(db):
    db.add(User(email="dup@waldorfwomenscare.com", group=UserGroup.BILLING))
    db.commit()
    from sqlalchemy.exc import IntegrityError
    db.add(User(email="dup@waldorfwomenscare.com", group=UserGroup.CLINICAL))
    try:
        db.commit()
        assert False, "expected IntegrityError on duplicate email"
    except IntegrityError:
        db.rollback()
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_user_model.py -v 2>&1 | tail -10
```
Expected: ImportError on `app.models.user`.

- [ ] **Step 3: Create `backend/app/models/user.py`**

```python
"""Minimal User model — email PK + group enum.

Phase 2a0 scope: one group per user, three fixed groups. Custom groups
and per-feature access levels come later (2a00).
"""
from sqlalchemy import Column, String, DateTime, Enum as SAEnum
from datetime import datetime
import enum
from app.database import Base


class UserGroup(str, enum.Enum):
    ADMIN = "admin"
    BILLING = "billing"
    CLINICAL = "clinical"


class User(Base):
    __tablename__ = "users"

    email = Column(String(200), primary_key=True)
    group = Column(SAEnum(UserGroup), default=UserGroup.CLINICAL, nullable=False, index=True)
    display_name = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

- [ ] **Step 4: Register model in `init_db()`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/database.py`. Line 23 currently reads:
```python
from app.models import patient, claim, payment, denial, appeal, audit, document, patient_directory, clinical, payment_analysis, fax_log, practice_config  # noqa
```
Change to:
```python
from app.models import patient, claim, payment, denial, appeal, audit, document, patient_directory, clinical, payment_analysis, fax_log, practice_config, user  # noqa
```

- [ ] **Step 5: Extend TEST_USER in conftest**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/conftest.py`. Change:
```python
TEST_USER = {"email": "tester@waldorfwomenscare.com", "name": "Test User"}
```
to:
```python
TEST_USER = {"email": "tester@waldorfwomenscare.com", "name": "Test User", "group": "admin"}
```

- [ ] **Step 6: Run tests**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_user_model.py tests/ -v 2>&1 | tail -10
```
Expected: 3 new tests PASS + all 34 prior tests PASS = 37 total.

- [ ] **Step 7: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/models/user.py backend/app/database.py backend/tests/test_user_model.py backend/tests/conftest.py
git commit -m "feat(backend): add User model with admin/billing/clinical group enum"
```

---

## Task 2: Extend `get_current_user` to upsert + include group

**Files:**
- Modify: `backend/app/routers/auth.py`
- Create: `backend/tests/test_auth_user_upsert.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_auth_user_upsert.py`:

```python
"""Tests for get_current_user upsert + /auth/me extension."""
from app.models.user import User, UserGroup


def test_me_returns_group_for_known_admin(client, db):
    # TEST_USER is admin — the conftest override bypasses the real upsert,
    # so just verify /auth/me returns the group field.
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "tester@waldorfwomenscare.com"
    assert body["group"] == "admin"


def test_get_current_user_upserts_new_user_as_clinical(db):
    """Direct call to get_current_user with a request lacking a row creates one."""
    from app.routers.auth import get_current_user, create_access_token
    from fastapi import Request
    from starlette.datastructures import Headers

    # Build a real token for a brand-new user.
    token = create_access_token({
        "email": "brandnew@waldorfwomenscare.com",
        "name": "Brand New",
    })

    # Fake a minimal Request with the Authorization header.
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    request = Request(scope)

    result = get_current_user(request, db=db)
    assert result["email"] == "brandnew@waldorfwomenscare.com"
    assert result["group"] == "clinical"

    row = db.query(User).filter(User.email == "brandnew@waldorfwomenscare.com").first()
    assert row is not None
    assert row.group == UserGroup.CLINICAL


def test_get_current_user_reads_existing_group(db):
    from app.routers.auth import get_current_user, create_access_token
    from fastapi import Request

    # Pre-seed a billing user.
    db.add(User(email="billing@waldorfwomenscare.com", group=UserGroup.BILLING))
    db.commit()

    token = create_access_token({
        "email": "billing@waldorfwomenscare.com",
        "name": "Billing User",
    })
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    request = Request(scope)

    result = get_current_user(request, db=db)
    assert result["group"] == "billing"


def test_get_current_user_normalizes_email_to_lowercase(db):
    from app.routers.auth import get_current_user, create_access_token
    from fastapi import Request

    token = create_access_token({
        "email": "MixedCase@waldorfwomenscare.com",
        "name": "Mixed",
    })
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    request = Request(scope)

    result = get_current_user(request, db=db)
    assert result["email"] == "mixedcase@waldorfwomenscare.com"
    row = db.query(User).filter(User.email == "mixedcase@waldorfwomenscare.com").first()
    assert row is not None
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_auth_user_upsert.py -v 2>&1 | tail -15
```
Expected: first test fails because `/auth/me` doesn't return `group`; remaining fail because `get_current_user` doesn't accept `db`.

- [ ] **Step 3: Extend `get_current_user` + `get_me`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/auth.py`.

Add these imports at the top with the existing imports:
```python
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User, UserGroup
from app.services.audit_service import log_action
```

Replace the entire existing `get_current_user` function with:

```python
def get_current_user(request: Request, db: Session = Depends(get_db)) -> dict:
    token = request.cookies.get("session_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    email = (payload.get("email") or "").lower().strip()
    if not email:
        raise HTTPException(status_code=401, detail="Token missing email")

    user_row = db.query(User).filter(User.email == email).first()
    if user_row is None:
        user_row = User(
            email=email,
            group=UserGroup.CLINICAL,
            display_name=payload.get("name"),
        )
        db.add(user_row)
        db.commit()
        db.refresh(user_row)
        log_action(db, "USER_CREATED", "user",
                   resource_id=email,
                   user_name=email,
                   description=f"Auto-created with default group clinical")

    return {
        "email": email,
        "name": payload.get("name") or user_row.display_name,
        "picture": payload.get("picture"),
        "group": user_row.group.value if hasattr(user_row.group, "value") else user_row.group,
    }
```

Replace the existing `get_me` function with:

```python
@router.get("/me")
def get_me(user: dict = Depends(get_current_user)):
    """Return current authenticated user (email, name, picture, group)."""
    return {
        "email": user.get("email"),
        "name": user.get("name"),
        "picture": user.get("picture"),
        "group": user.get("group"),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_auth_user_upsert.py tests/ -v 2>&1 | tail -15
```
Expected: 4 new + 37 prior = 41 PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/auth.py backend/tests/test_auth_user_upsert.py
git commit -m "feat(backend): get_current_user upserts User, /auth/me returns group"
```

---

## Task 3: `require_group` dependency factory

**Files:**
- Modify: `backend/app/routers/auth.py`
- Create: `backend/tests/test_require_group.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_require_group.py`:

```python
"""Tests for require_group FastAPI dependency factory."""
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from app.routers.auth import get_current_user, require_group


def test_require_group_allows_matching_group():
    app = FastAPI()
    r = APIRouter()

    @r.get("/probe")
    def _probe(_: dict = Depends(require_group("admin", "billing"))):
        return {"ok": True}

    app.include_router(r)

    # Override to simulate an admin
    app.dependency_overrides[get_current_user] = lambda: {
        "email": "a@b.com", "group": "admin", "name": "A",
    }

    with TestClient(app) as client:
        assert client.get("/probe").status_code == 200


def test_require_group_blocks_wrong_group():
    app = FastAPI()
    r = APIRouter()

    @r.get("/probe")
    def _probe(_: dict = Depends(require_group("admin", "billing"))):
        return {"ok": True}

    app.include_router(r)

    app.dependency_overrides[get_current_user] = lambda: {
        "email": "c@b.com", "group": "clinical", "name": "C",
    }

    with TestClient(app) as client:
        r = client.get("/probe")
        assert r.status_code == 403
        assert "forbidden" in r.json()["detail"].lower()


def test_require_group_accepts_multiple_groups():
    app = FastAPI()
    r = APIRouter()

    @r.get("/probe")
    def _probe(_: dict = Depends(require_group("billing"))):
        return {"ok": True}

    app.include_router(r)

    app.dependency_overrides[get_current_user] = lambda: {
        "email": "b@b.com", "group": "billing", "name": "B",
    }

    with TestClient(app) as client:
        assert client.get("/probe").status_code == 200
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_require_group.py -v 2>&1 | tail -8
```
Expected: ImportError on `require_group`.

- [ ] **Step 3: Add `require_group` to `auth.py`**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/auth.py`:

```python
def require_group(*allowed: str):
    """FastAPI dependency factory: 403 if current user's group isn't in allowed."""
    def _dep(current_user: dict = Depends(get_current_user)):
        if current_user.get("group") not in allowed:
            raise HTTPException(status_code=403, detail="forbidden")
        return current_user
    return _dep
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_require_group.py -v 2>&1 | tail -8
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/auth.py backend/tests/test_require_group.py
git commit -m "feat(backend): require_group dependency factory (403 if group not allowed)"
```

---

## Task 4: Attach `require_group` to billing routers

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_billing_router_guards.py`
- Modify: `backend/tests/conftest.py` (add clinical client helper)

- [ ] **Step 1: Add a `clinical_client` fixture**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/conftest.py`:

```python
CLINICAL_USER = {"email": "clinician@waldorfwomenscare.com", "name": "Clinician", "group": "clinical"}


@pytest.fixture
def clinical_client(db):
    """Same as `client` but the authenticated user has group=clinical."""
    def override_get_db():
        try:
            yield db
        finally:
            pass
    def override_get_current_user():
        return CLINICAL_USER

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Write the failing guard tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_billing_router_guards.py`:

```python
"""Every billing-only router must 403 a clinical user."""
import pytest

BILLING_ROUTES = [
    "/api/claims",
    "/api/claims/summary",
    "/api/denials",
    "/api/denials/summary",
    "/api/appeals",
    "/api/audit",
    "/api/waystar/status",
    "/api/ar/summary",
    "/api/imports/recent",
    "/api/dashboard/summary",
]


CLINICAL_FAX_ROUTES = [
    "/api/fax/recent",
    "/api/fax-log",
]


@pytest.mark.parametrize("path", BILLING_ROUTES)
def test_clinical_user_forbidden_on_billing_routes(clinical_client, path):
    r = clinical_client.get(path)
    assert r.status_code == 403, f"Expected 403 on {path}, got {r.status_code}: {r.text[:200]}"


CLINICAL_ROUTES = [
    "/api/auth/me",
    "/api/documents/index/status",
] + CLINICAL_FAX_ROUTES


@pytest.mark.parametrize("path", CLINICAL_ROUTES)
def test_clinical_user_allowed_on_clinical_routes(clinical_client, path):
    r = clinical_client.get(path)
    assert r.status_code == 200, f"Expected 200 on {path}, got {r.status_code}: {r.text[:200]}"
```

- [ ] **Step 3: Run to verify failures**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_billing_router_guards.py -v 2>&1 | tail -20
```
Expected: the 12 billing-route tests FAIL (200 instead of 403) because no guard is applied yet. The 2 clinical-route tests should pass.

- [ ] **Step 4: Attach guards in `main.py`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/main.py`.

Find the block of `app.include_router(...)` calls. Above the first one, add:
```python
from fastapi import Depends
BILLING = [Depends(auth.require_group("admin", "billing"))]
```

Change these specific routers to include `dependencies=BILLING` (keep the existing `prefix="/api"`):

Replace the existing lines for these 9 routers:
```python
app.include_router(imports.router, prefix="/api")
app.include_router(claims.router, prefix="/api")
app.include_router(denials.router, prefix="/api")
app.include_router(appeals.router, prefix="/api")
app.include_router(eob.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(waystar.router, prefix="/api")
app.include_router(ar.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
```
With:
```python
app.include_router(imports.router, prefix="/api", dependencies=BILLING)
app.include_router(claims.router, prefix="/api", dependencies=BILLING)
app.include_router(denials.router, prefix="/api", dependencies=BILLING)
app.include_router(appeals.router, prefix="/api", dependencies=BILLING)
app.include_router(eob.router, prefix="/api", dependencies=BILLING)
app.include_router(audit.router, prefix="/api", dependencies=BILLING)
app.include_router(waystar.router, prefix="/api", dependencies=BILLING)
app.include_router(ar.router, prefix="/api", dependencies=BILLING)
app.include_router(dashboard.router, prefix="/api", dependencies=BILLING)
```

LEAVE these untouched (clinical-accessible — fax is document-adjacent):
```python
app.include_router(patients.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(intake.router, prefix="/api")
app.include_router(chart.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(fax.router, prefix="/api")
app.include_router(fax_batch.router, prefix="/api")
app.include_router(fax_batch.log_router, prefix="/api")
```

- [ ] **Step 5: Run tests**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_billing_router_guards.py tests/ -v 2>&1 | tail -20
```
Expected: 9 billing-403 + 4 clinical-200 = 13 guard tests PASS + all 41 prior tests PASS = 54 total.

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/main.py backend/tests/test_billing_router_guards.py backend/tests/conftest.py
git commit -m "feat(backend): attach require_group to 12 billing-only routers"
```

---

## Task 5: Seed script

**Files:**
- Create: `backend/scripts/seed_users.py`

- [ ] **Step 1: Create the seed script**

Write `/Users/wwcclaudecode/Documents/wwc-era-project/backend/scripts/seed_users.py`:

```python
"""Idempotent user seeder. Inserts/updates users with explicit group assignments.

Safe to run multiple times. Edit the USERS list to add/change coworkers.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.user import User, UserGroup


USERS = [
    ("ocooke@waldorfwomenscare.com", UserGroup.ADMIN, "Owner"),
]


def main():
    init_db()
    db = SessionLocal()
    try:
        for email, group, display_name in USERS:
            email = email.lower().strip()
            existing = db.query(User).filter(User.email == email).first()
            if existing is None:
                db.add(User(email=email, group=group, display_name=display_name))
                print(f"  [add]    {email} -> {group.value}")
            elif existing.group != group or existing.display_name != display_name:
                existing.group = group
                existing.display_name = display_name
                print(f"  [update] {email} -> {group.value}")
            else:
                print(f"  [skip]   {email} (already {group.value})")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run against the real DB**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python scripts/seed_users.py
```
Expected:
```
  [add]    ocooke@waldorfwomenscare.com -> admin
```

Run it again and confirm idempotency:
```bash
python scripts/seed_users.py
```
Expected:
```
  [skip]   ocooke@waldorfwomenscare.com (already admin)
```

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/scripts/seed_users.py
git commit -m "chore(backend): seed_users.py (idempotent user/group seeder)"
```

---

## Task 6: Frontend `useCurrentUser` hook

**Files:**
- Create: `frontend/src/hooks/useCurrentUser.js`

- [ ] **Step 1: Create the hook**

Write `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/hooks/useCurrentUser.js`:

```jsx
import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Returns the currently authenticated user plus convenience flags.
 * {email, name, picture, group, isAdmin, isBilling, isClinical, canSeeBilling}
 *
 * While loading, every flag is false and `group` is undefined — callers should
 * gate clinical-hiding UI on `canSeeBilling` (false during load is the safer
 * default for clinical-like screens).
 */
export function useCurrentUser() {
  const q = useQuery({
    queryKey: ['current-user'],
    queryFn: () => api.get('/auth/me').then(r => r.data),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const data = q.data || {}
  const group = data.group
  return {
    email: data.email,
    name: data.name,
    picture: data.picture,
    group,
    isAdmin: group === 'admin',
    isBilling: group === 'billing',
    isClinical: group === 'clinical',
    canSeeBilling: group === 'admin' || group === 'billing',
    isLoading: q.isLoading,
  }
}
```

- [ ] **Step 2: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/hooks/useCurrentUser.js
git commit -m "feat(frontend): useCurrentUser hook (reads /auth/me, exposes group flags)"
```

---

## Task 7: TopNav filter + clinical landing-route redirect

**Files:**
- Modify: `frontend/src/components/layout/TopNav.jsx`
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Filter nav by group in TopNav**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/components/layout/TopNav.jsx`.

Add this import at the top (with the existing imports):
```jsx
import { useCurrentUser } from '../../hooks/useCurrentUser'
```

Find the existing `const nav = [...]` array (8 items). Leave it as-is — it's the full nav for billing/admin.

Add a second array right after it:
```jsx
const CLINICAL_NAV = [
  { to: '/documents', label: 'Charts' },
]
```

Find the component body — something like `export default function TopNav({ user, onLogout }) {`. Right at the top of the component body, add:
```jsx
  const { isClinical } = useCurrentUser()
  const visibleNav = isClinical ? CLINICAL_NAV : nav
```

Find the `{nav.map(({ to, label }) => (` line inside the JSX and change it to `{visibleNav.map(({ to, label }) => (`. Leave everything else unchanged.

- [ ] **Step 2: Add post-login + clinical-path redirect in `App.jsx`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/App.jsx`.

Add this import at the top (with the others):
```jsx
import { useCurrentUser } from './hooks/useCurrentUser'
```

Inside `ProtectedApp({ user, onLogout })`, at the top of the function body, add:
```jsx
  const { isClinical, isLoading } = useCurrentUser()
```

Find the `<Routes>` block. Wrap the `/` route so clinical users get redirected to `/documents`:

```jsx
          <Routes>
            <Route path="/" element={
              isLoading ? null : (isClinical ? <Navigate to="/documents" replace /> : <Dashboard />)
            } />
            <Route path="/ar"                  element={<ARDashboard />} />
            <Route path="/documents"           element={<Documents />} />
            <Route path="/chart/:chartNumber"  element={<PatientChart />} />
            <Route path="/claims"              element={<Claims />} />
            <Route path="/claims/:id"          element={<ClaimDetail />} />
            <Route path="/patients"            element={<Patients />} />
            <Route path="/patients/:id"        element={<PatientDetail />} />
            <Route path="/denials"             element={<Denials />} />
            <Route path="/appeals"             element={<Appeals />} />
            <Route path="/import"              element={<ImportFiles />} />
            <Route path="/audit"               element={<AuditLog />} />
            <Route path="*"                    element={<Navigate to="/" />} />
          </Routes>
```

- [ ] **Step 3: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 4: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/components/layout/TopNav.jsx frontend/src/App.jsx
git commit -m "feat(frontend): TopNav filters by group, clinical users land on /documents"
```

---

## Task 8: (removed — scope narrowed)

The original T8 hid the fax-log pane and fax chips on `Documents.jsx` for clinical users. Scope was narrowed: clinical users CAN see/use everything document-related including fax log and fax chips. No code change required on `Documents.jsx`.

**Skip this task entirely and move to T9.**

## Task 8-ORIGINAL-DEPRECATED: Hide fax-log pane from clinical users

**Files:**
- Modify: `frontend/src/pages/Documents.jsx`

- [ ] **Step 1: Add the guard**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/Documents.jsx`.

Add this import at the top (with the others):
```jsx
import { useCurrentUser } from '../hooks/useCurrentUser'
```

Near the top of the `Documents` component body (after the existing state declarations and before the queries), add:
```jsx
  const { canSeeBilling } = useCurrentUser()
```

Find the `{/* Two-pane layout */}` div — the one with `style={{ gridTemplateColumns: '280px 1fr', ... }}`. Change it so the grid collapses to a single column when the user is clinical. Replace the div opener:

From:
```jsx
      <div className="grid gap-3" style={{ gridTemplateColumns: '280px 1fr', minHeight: 'calc(100vh - 180px)' }}>
```
To:
```jsx
      <div className="grid gap-3" style={{ gridTemplateColumns: canSeeBilling ? '280px 1fr' : '1fr', minHeight: 'calc(100vh - 180px)' }}>
```

Find the `<FaxLogPane />` at the bottom of that grid. Wrap it in a conditional:
```jsx
        {canSeeBilling && <FaxLogPane />}
```

- [ ] **Step 2: Hide the per-patient fax chip for clinical**

In the same file, find the line in the patient list that renders:
```jsx
                  <div className="shrink-0">{faxChip(faxSummary?.[p.chart_number])}</div>
```
Wrap the chip render in a conditional:
```jsx
                  {canSeeBilling && <div className="shrink-0">{faxChip(faxSummary?.[p.chart_number])}</div>}
```

Also gate the `useChartFaxSummary` hook call — it queries a billing endpoint. Find:
```jsx
  const { data: faxSummary } = useChartFaxSummary()
```
Change to:
```jsx
  const { data: faxSummary } = useChartFaxSummary({ enabled: canSeeBilling })
```

Note — `useChartFaxSummary` currently doesn't accept an `options` argument. Update the hook as a tiny companion edit:

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/hooks/useChartFaxSummary.js`. Change the function signature from:
```jsx
export function useChartFaxSummary() {
  return useQuery({
    queryKey: ['fax-chart-summary'],
```
To:
```jsx
export function useChartFaxSummary({ enabled = true } = {}) {
  return useQuery({
    queryKey: ['fax-chart-summary'],
    enabled,
```
Add the `enabled` key into the `useQuery` config (keep all existing keys).

- [ ] **Step 3: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 4: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/Documents.jsx frontend/src/hooks/useChartFaxSummary.js
git commit -m "feat(frontend): hide fax-log pane + chips from clinical users on Charts"
```

---

## Task 9: (removed — scope narrowed)

The original T9 hid the batch-fax UI, status chips, and FaxBatchModal on `PatientChart.jsx` for clinical users. Scope was narrowed: clinical users CAN use the batch-fax flow, retry, and see chips. No code change required on `PatientChart.jsx`.

**Skip this task entirely and move to T10.**

## Task 9-ORIGINAL-DEPRECATED: Hide batch-fax affordances from clinical on PatientChart

**Files:**
- Modify: `frontend/src/pages/PatientChart.jsx`
- Modify: `frontend/src/hooks/useFaxByChart.js`

- [ ] **Step 1: Add `enabled` option to `useFaxByChart`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/hooks/useFaxByChart.js`.

The current signature is:
```jsx
export function useFaxByChart(chartNumber, { enabled = true } = {}) {
  return useQuery({
    queryKey: ['fax-by-chart', chartNumber],
    queryFn: () => api.get(`/fax/by-chart/${chartNumber}`).then(r => r.data),
    enabled: !!chartNumber && enabled,
    ...
```

It already takes an `enabled` option — no change needed. Just confirm by reading the file. If the signature differs, update it to accept `{ enabled = true } = {}` so the caller can gate the query on `canSeeBilling`.

- [ ] **Step 2: Guard the batch-fax UI in PatientChart**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/PatientChart.jsx`.

Add the import at the top (alongside existing imports):
```jsx
import { useCurrentUser } from '../hooks/useCurrentUser'
```

Find the `DocumentsSection` component (around line 229 pre-rewrite; find the one that uses `useFaxByChart`). At the top of its body, add:
```jsx
  const { canSeeBilling } = useCurrentUser()
```

Pass `canSeeBilling` into the `useFaxByChart` call. Change:
```jsx
  const faxQuery = useFaxByChart(chartNumber)
```
To:
```jsx
  const faxQuery = useFaxByChart(chartNumber, { enabled: canSeeBilling })
```

Find the action-bar section (the div with "Select unsent" / "Clear" / "Fax N docs to EMA →" buttons at the top of `DocumentsSection`). Wrap the whole action bar in `{canSeeBilling && ( ... )}`. Specifically, find the line that renders:
```jsx
        <div className="ml-auto flex items-center gap-3 text-xs">
          <button onClick={selectUnsent} ...>Select unsent</button>
          {selected.size > 0 && ( ... )}
        </div>
```
Wrap it:
```jsx
        {canSeeBilling && (
          <div className="ml-auto flex items-center gap-3 text-xs">
            <button onClick={selectUnsent} ...>Select unsent</button>
            {selected.size > 0 && ( ... )}
          </div>
        )}
```

Find the per-doc checkbox `<input type="checkbox" ...>` inside the doc row render — wrap it in `{canSeeBilling && ...}`:
```jsx
                  {canSeeBilling && (
                    <input
                      type="checkbox"
                      className="mr-3"
                      checked={selected.has(doc.id)}
                      onChange={() => toggleDoc(doc.id)}
                    />
                  )}
```

Find the `FaxStatusChip` render inside the doc row — wrap it in `{canSeeBilling && ...}`:
```jsx
                  <span className="mr-auto">
                    {canSeeBilling && faxRow && <FaxStatusChip row={faxRow} onRetry={handleRetry} />}
                  </span>
```

(If the existing render already has `{faxRow && <FaxStatusChip ...>}`, change the condition to `{canSeeBilling && faxRow && <FaxStatusChip ...>}`.)

Find the main `PatientChart` default export. The `FaxBatchModal` render is also billing-only. Find:
```jsx
{batchDocIds && (
  <FaxBatchModal ... />
)}
```
Change to:
```jsx
{canSeeBilling && batchDocIds && (
  <FaxBatchModal ... />
)}
```

Add `const { canSeeBilling } = useCurrentUser()` at the top of the `PatientChart` default export body too.

- [ ] **Step 3: Build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 4: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/PatientChart.jsx frontend/src/hooks/useFaxByChart.js
git commit -m "feat(frontend): hide batch-fax UI + status chips from clinical on PatientChart"
```

---

## Task 10: Final verification

**Files:** none — verification only.

- [ ] **Step 1: Full backend test suite**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ -v 2>&1 | tail -20
```
Expected: all tests PASS (Phase 1 + Phase 2 backend + 2a0).

- [ ] **Step 2: Frontend build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -5
```
Expected: success.

- [ ] **Step 3: Manual smoke — billing user (you)**

Start the stack (`./start.sh` from repo root). Sign in as `ocooke@waldorfwomenscare.com`:
- TopNav shows all 8 entries.
- `/` renders the Dashboard as usual.
- `/documents` shows the two-pane layout.
- `/chart/<any-chart>` shows the batch-fax UI and status chips.

- [ ] **Step 4: Manual smoke — clinical user**

To test without signing in as a new Google account, temporarily edit the `users` table directly:
```bash
sqlite3 /Users/wwcclaudecode/Documents/wwc-era-project/backend/era_data.db "UPDATE users SET \"group\" = 'clinical' WHERE email = 'ocooke@waldorfwomenscare.com';"
```
Then hard-refresh the browser:
- TopNav shows only "Charts".
- Navigating to `/` redirects to `/documents`.
- `/documents` renders the full two-pane Charts page (patient list + Recent faxes) — clinical has full document access including fax.
- `/chart/<any-chart>` renders patient info + docs + the batch-fax action bar + checkboxes + status chips — clinical handles fax workflow too.
- Directly hitting `http://localhost:8000/api/claims` in the browser returns 403.
- Directly hitting `http://localhost:8000/api/fax/recent` returns 200 (fax is document-adjacent, clinical-allowed).

Restore your group:
```bash
sqlite3 /Users/wwcclaudecode/Documents/wwc-era-project/backend/era_data.db "UPDATE users SET \"group\" = 'admin' WHERE email = 'ocooke@waldorfwomenscare.com';"
```

- [ ] **Step 5: Final empty commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git commit --allow-empty -m "test: 2a0 Groups & page-visibility verified end-to-end"
```

---

## Self-review

- **Spec coverage:** ✓
  - User table + enum → T1
  - `get_current_user` upsert + lowercase normalize + audit log → T2
  - `/auth/me` returns group → T2
  - `require_group` helper + tests → T3
  - Router-level guards on 12 billing endpoints → T4
  - Seed script → T5
  - `useCurrentUser` hook → T6
  - TopNav filter + clinical redirect to `/documents` → T7
  - Hide fax-log pane + chips on Charts → T8
  - Hide batch-fax UI on PatientChart → T9
  - Manual smoke tests (billing + clinical) → T10

- **Placeholder scan:** ✓ No "TBD", no "handle appropriately", no "similar to". Each step shows exact code or exact command.

- **Type consistency:** ✓
  - `UserGroup` enum values `"admin"/"billing"/"clinical"` used consistently in T1, T2, T3, T4, T5, T6.
  - `get_current_user` returns `{email, name, picture, group}` — consumed by `/auth/me` (T2), `require_group` (T3), `useCurrentUser` (T6).
  - `canSeeBilling` flag defined in T6 and consumed identically in T8 + T9.
  - `useChartFaxSummary` signature update in T8 is backward-compat for existing callers (optional opts, default `enabled: true`).
