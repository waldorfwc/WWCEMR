# Consolidated Module Manuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the duplicated LARC/Pellet manual machinery with one reusable, module-keyed manual system, migrate the existing manuals onto it without losing edits, add a staleness badge, author manuals for the major modules, and adopt a keep-in-sync convention.

**Architecture:** One `manual_sections` table keyed by `module`; one `/api/manual` CRUD router with per-module tier checks; one shared `<ModuleManual>` React component; one idempotent seed registry. An edit-safe one-shot migration copies the existing `larc_manual_sections` + `pellet_manual_sections` rows into the unified table. Then per-module content + nav links are added.

**Tech Stack:** FastAPI, SQLAlchemy, pytest; React + Vite, @tanstack/react-query, marked + DOMPurify. Datetimes via `app.utils.dt.now_utc_naive`.

**Spec:** `docs/superpowers/specs/2026-06-21-module-manuals-design.md`

---

## Background for the implementer (read once)

Current state (both are copy-paste twins):
- Models `LarcManualSection` (`app/models/larc.py`), `PelletManualSection` (`app/models/pellet.py`): columns `id, slug(unique), title, body_md, sort_order, created_at, updated_at, updated_by`.
- Routers: `GET/POST/PATCH/DELETE /larc/manual` (in `app/routers/larc.py`, ~lines 3695-3785) and the same under `/pellets/manual` (in `app/routers/pellet.py`). Gated by `requires_tier(Module.LARC/PELLETS, ...)`.
- Seeds: `SEED_MANUAL_SECTIONS` lists + `seed_larc_manual()` / `seed_pellet_manual()` in `app/services/larc/seed.py` and `app/services/pellet/seed.py`. Called at the end of `seed_larc_device_types()` (line 97) and `seed_pellet_dose_types()` (line 62). Both are idempotent "only add missing slug" loops over `(slug, title, sort_order, body)` tuples.
- Frontend pages `frontend/src/pages/LarcManual.jsx` and `PelletManual.jsx` (near-identical): TOC + `marked`+`DOMPurify` render + in-app editing; query keys `['larc-manual']` / (pellet uses its own). Routes in `frontend/src/routes.jsx` (`/larc/manual`, `/pellets/manual`).

Permissions primitives (all exist):
- `get_current_user` — `app/routers/auth.py:69` — base auth dependency returning `current_user: dict` (has `.get("email")`).
- `effective_tier(db, user_email, module) -> Tier` — `app/permissions/resolver.py:32`.
- `Tier` is an `IntEnum` (`app/permissions/catalog.py:35`): `NONE=0 < VIEW=10 < WORK=20 < MANAGE=30 < ADMIN < SUPER_ADMIN`. Compare with `<`.
- `Module` enum (`app/permissions/catalog.py`): keys are strings — `surgery`, `active_ar`, `billing_bank_recon`, `billing_missing_charges`, `billing_insurance_docs`, `chart`, `recall`, `reputation`, `training`, `device_larc`, `pellets`.
- `MODULE_REGISTRY[module].label` gives the human label.
- `requires_tier` (`app/permissions/dependencies.py:71`) is the route-time gate; we mirror its body in a runtime `assert_tier`.

`init_db()` (`app/database.py:38`): `create_all()` → `_apply_lightweight_migrations()` → `_migrate_*()` → seed functions (lines 48-67).

Project python for tests: `backend/venv/bin/python`. Run one test:
`cd backend && ./venv/bin/python -m pytest tests/<file>::<name> -v`

---

## File Structure

- Create `backend/app/models/manual.py` — `ManualSection` model.
- Modify `backend/app/database.py` — add `_migrate_manuals_to_unified()`, call it + `seed_manuals()` in `init_db`; drop the old per-module manual seed calls.
- Create `backend/app/services/manual_seed.py` — `MANUAL_SEEDS` registry + `seed_manuals()`; holds all per-module section lists.
- Modify `backend/app/services/larc/seed.py`, `backend/app/services/pellet/seed.py` — remove their `SEED_MANUAL_SECTIONS` + `seed_*_manual()` and the call sites.
- Create `backend/app/routers/manual.py` — unified CRUD router + `assert_tier` + `MODULE_BY_KEY`.
- Modify `backend/app/main.py` — mount `manual.router`; (old `/larc/manual`, `/pellets/manual` handlers removed from their routers).
- Modify `backend/app/routers/larc.py`, `backend/app/routers/pellet.py` — delete the manual CRUD handlers.
- Create `frontend/src/components/manual/ModuleManual.jsx` — shared component + staleness badge.
- Delete `frontend/src/pages/LarcManual.jsx`, `frontend/src/pages/PelletManual.jsx`.
- Modify `frontend/src/routes.jsx` — point `/larc/manual` + `/pellets/manual` at `<ModuleManual>`; add `manual` routes for the new modules.
- Modify per-module nav/header files (listed in Phase 2) — add a Manual link.
- Tests: `backend/tests/test_manual_*.py`.

---

# Phase 1 — Framework (independently shippable)

### Task 1: `ManualSection` model

**Files:**
- Create: `backend/app/models/manual.py`
- Test: `backend/tests/test_manual_model.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_manual_model.py
from app.models.manual import ManualSection


def test_manual_section_columns_and_table(db):
    row = ManualSection(module="device_larc", slug="overview", title="Overview",
                        body_md="hi", sort_order=10, updated_by="system:seed")
    db.add(row); db.commit(); db.refresh(row)
    assert row.id is not None
    assert row.module == "device_larc"
    assert row.created_at is not None and row.updated_at is not None


def test_manual_section_unique_per_module(db):
    db.add(ManualSection(module="surgery", slug="overview", title="A", body_md=""))
    db.add(ManualSection(module="pellets", slug="overview", title="B", body_md=""))
    db.commit()  # same slug, different module -> OK
    from sqlalchemy.exc import IntegrityError
    db.add(ManualSection(module="surgery", slug="overview", title="dup", body_md=""))
    try:
        db.commit()
        assert False, "expected unique violation on (module, slug)"
    except IntegrityError:
        db.rollback()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_manual_model.py -v`
Expected: FAIL — `ModuleNotFoundError: app.models.manual`.

- [ ] **Step 3: Implement the model**

```python
# backend/app/models/manual.py
from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from app.database import Base
from app.models.guid import GUID, new_uuid      # match the import used by app/models/larc.py
from app.utils.dt import now_utc_naive


class ManualSection(Base):
    """One section of a module's in-app operating manual. Keyed by `module`
    (a Module enum string) so every module shares one table + one API."""
    __tablename__ = "manual_sections"
    __table_args__ = (
        UniqueConstraint("module", "slug", name="uq_manual_module_slug"),
        Index("ix_manual_module", "module"),
    )

    id = Column(GUID(), primary_key=True, default=new_uuid)
    module = Column(String(40), nullable=False)
    slug = Column(String(80), nullable=False)            # TOC anchor, unique per module
    title = Column(String(200), nullable=False)
    body_md = Column(Text, nullable=False, default="")
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    updated_at = Column(DateTime, default=now_utc_naive, onupdate=now_utc_naive,
                        nullable=False)
    updated_by = Column(String(200), nullable=True)
```

Note: confirm the GUID import path matches `app/models/larc.py` (it imports `GUID, new_uuid`); copy that exact import line if it differs from above.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_manual_model.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Ensure the model is imported so `create_all` sees it**

Add `from app.models import manual  # noqa` wherever the app aggregates model modules (grep for where `app.models.larc` is imported for metadata, e.g. `app/database.py` or `app/models/__init__.py`). Verify `python -c "import app.main"` still imports.

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/models/manual.py backend/tests/test_manual_model.py backend/app/models/__init__.py backend/app/database.py
git commit -m "feat(manual): ManualSection model (module-keyed)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Edit-safe migration of existing manuals

**Files:**
- Modify: `backend/app/database.py` (add `_migrate_manuals_to_unified()`, call in `init_db` after `_apply_lightweight_migrations()`)
- Test: `backend/tests/test_manual_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_manual_migration.py
from sqlalchemy import text
from app.models.manual import ManualSection
from app.database import _migrate_manuals_to_unified


def _make_old_table(db, name):
    db.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {name} (
            id VARCHAR(36), slug VARCHAR(80), title VARCHAR(200), body_md TEXT,
            sort_order INTEGER, created_at TIMESTAMP, updated_at TIMESTAMP,
            updated_by VARCHAR(200))"""))
    db.commit()


def test_migration_copies_edits_and_is_idempotent(db):
    _make_old_table(db, "larc_manual_sections")
    db.execute(text("""INSERT INTO larc_manual_sections
        (id, slug, title, body_md, sort_order, created_at, updated_at, updated_by)
        VALUES ('x1','overview','Overview','EDITED BODY',10,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'staff@wwc.com')"""))
    db.commit()

    _migrate_manuals_to_unified(db)

    rows = db.query(ManualSection).filter_by(module="device_larc").all()
    assert len(rows) == 1
    assert rows[0].slug == "overview" and rows[0].body_md == "EDITED BODY"
    assert rows[0].updated_by == "staff@wwc.com"

    # idempotent: re-run adds nothing, body unchanged
    _migrate_manuals_to_unified(db)
    rows2 = db.query(ManualSection).filter_by(module="device_larc").all()
    assert len(rows2) == 1 and rows2[0].body_md == "EDITED BODY"
```

(`_migrate_manuals_to_unified` takes an optional `db` for the test; in `init_db` it opens its own session.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_manual_migration.py -v`
Expected: FAIL — `_migrate_manuals_to_unified` not defined.

- [ ] **Step 3: Implement the migration**

In `backend/app/database.py` add (and import `inspect`, `text` from sqlalchemy if not present):

```python
def _migrate_manuals_to_unified(db=None):
    """One-shot, edit-safe copy of the per-module manual tables into the
    unified manual_sections table. Copies the CURRENT row (incl. practice
    edits) only when the (module, slug) target doesn't already exist, so it
    never clobbers and is safe to re-run. Old tables are left as backup."""
    from sqlalchemy import inspect as _inspect, text as _text
    from app.models.manual import ManualSection
    own = db is None
    if own:
        db = SessionLocal()
    try:
        insp = _inspect(db.get_bind())
        existing_tables = set(insp.get_table_names())
        sources = [("larc_manual_sections", "device_larc"),
                   ("pellet_manual_sections", "pellets")]
        for table, module in sources:
            if table not in existing_tables:
                continue
            have = {s.slug for s in db.query(ManualSection).filter_by(module=module).all()}
            rows = db.execute(_text(
                f"SELECT slug, title, body_md, sort_order, created_at, "
                f"updated_at, updated_by FROM {table}")).mappings().all()
            added = 0
            for r in rows:
                if r["slug"] in have:
                    continue
                db.add(ManualSection(
                    module=module, slug=r["slug"], title=r["title"],
                    body_md=r["body_md"] or "", sort_order=r["sort_order"] or 0,
                    created_at=r["created_at"], updated_at=r["updated_at"],
                    updated_by=r["updated_by"]))
                added += 1
            if added:
                db.commit()
    finally:
        if own:
            db.close()
```

Wire it into `init_db()` right after `_apply_lightweight_migrations()`:

```python
    _apply_lightweight_migrations()
    _migrate_manuals_to_unified()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_manual_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/database.py backend/tests/test_manual_migration.py
git commit -m "feat(manual): edit-safe migration of LARC/Pellet manuals into unified table

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Seed registry + `seed_manuals()` (LARC + Pellet moved in)

**Files:**
- Create: `backend/app/services/manual_seed.py`
- Modify: `backend/app/services/larc/seed.py` (remove `SEED_MANUAL_SECTIONS` + `seed_larc_manual` + its call at line 97)
- Modify: `backend/app/services/pellet/seed.py` (remove `SEED_MANUAL_SECTIONS` + `seed_pellet_manual` + its call at line 62)
- Modify: `backend/app/database.py` (call `seed_manuals()` in `init_db`)
- Test: `backend/tests/test_manual_seed.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_manual_seed.py
from app.models.manual import ManualSection
from app.services.manual_seed import seed_manuals, MANUAL_SEEDS


def test_seed_is_additive_only(db):
    db.add(ManualSection(module="device_larc", slug="overview",
                        title="Custom", body_md="PRACTICE EDIT", sort_order=10))
    db.commit()
    seed_manuals(db)
    overview = (db.query(ManualSection)
                  .filter_by(module="device_larc", slug="overview").one())
    assert overview.body_md == "PRACTICE EDIT"          # not clobbered
    # other LARC slugs got added
    n = db.query(ManualSection).filter_by(module="device_larc").count()
    assert n > 1


def test_every_registered_module_seeds_at_least_one(db):
    seed_manuals(db)
    for module in MANUAL_SEEDS:
        n = db.query(ManualSection).filter_by(module=module).count()
        assert n >= 1, f"{module} seeded nothing"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_manual_seed.py -v`
Expected: FAIL — `app.services.manual_seed` missing.

- [ ] **Step 3: Create the registry + seeder; move LARC + Pellet content in**

Create `backend/app/services/manual_seed.py`. Move the existing LARC `SEED_MANUAL_SECTIONS` list (from `app/services/larc/seed.py`, INCLUDING the `checkout-quick-action` and `device-ownership` sections added 2026-06-21) and the Pellet list (from `app/services/pellet/seed.py`) verbatim into module-named constants here:

```python
from app.database import SessionLocal
from app.models.manual import ManualSection

LARC_MANUAL_SECTIONS = [ ... ]      # moved verbatim from larc/seed.py
PELLET_MANUAL_SECTIONS = [ ... ]    # moved verbatim from pellet/seed.py

MANUAL_SEEDS = {
    "device_larc": LARC_MANUAL_SECTIONS,
    "pellets":     PELLET_MANUAL_SECTIONS,
    # Phase 2 modules append their lists here.
}


def seed_manuals(db=None):
    """Idempotent: insert only (module, slug) rows that don't already exist."""
    own = db is None
    if own:
        db = SessionLocal()
    try:
        for module, sections in MANUAL_SEEDS.items():
            have = {s.slug for s in db.query(ManualSection).filter_by(module=module).all()}
            added = 0
            for slug, title, sort_order, body in sections:
                if slug in have:
                    continue
                db.add(ManualSection(module=module, slug=slug, title=title,
                                     sort_order=sort_order, body_md=body,
                                     updated_by="system:seed"))
                added += 1
            if added:
                db.commit()
    finally:
        if own:
            db.close()
```

Then in `app/services/larc/seed.py`: delete the `SEED_MANUAL_SECTIONS` list and the `seed_larc_manual` function, and remove the `seed_larc_manual()` call (line ~97). Same for `app/services/pellet/seed.py` (remove its list, `seed_pellet_manual`, and the call at ~line 62). In `app/database.py init_db()`, replace those (now-removed) seed effects by calling `seed_manuals()` once — add after the existing seed block:

```python
    from app.services.manual_seed import seed_manuals
    seed_manuals()
```

Keep the migration BEFORE the seed in `init_db` order (migration copies edited rows; seed then only fills genuinely-missing slugs).

- [ ] **Step 4: Run to verify it passes + nothing else broke**

Run:
```bash
cd backend && ./venv/bin/python -m pytest tests/test_manual_seed.py -v
./venv/bin/python -c "import app.main"   # imports cleanly after removing old seed fns
```
Expected: tests PASS; import OK.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/manual_seed.py backend/app/services/larc/seed.py backend/app/services/pellet/seed.py backend/app/database.py backend/tests/test_manual_seed.py
git commit -m "feat(manual): unified seed registry; move LARC/Pellet sections in

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Unified `/api/manual` router with per-module tier checks

**Files:**
- Create: `backend/app/routers/manual.py`
- Modify: `backend/app/main.py` (mount the router)
- Modify: `backend/app/routers/larc.py`, `backend/app/routers/pellet.py` (delete the old manual CRUD handlers)
- Test: `backend/tests/test_manual_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_manual_api.py
from app.models.manual import ManualSection


def test_list_requires_module_view(client, db, grant):
    db.add(ManualSection(module="surgery", slug="overview", title="O",
                        body_md="x", sort_order=10)); db.commit()
    # no surgery access -> 403
    r = client.get("/api/manual?module=surgery")
    assert r.status_code == 403
    grant("surgery", "VIEW")
    r = client.get("/api/manual?module=surgery")
    assert r.status_code == 200 and r.json()[0]["slug"] == "overview"


def test_unknown_module_400(client, grant):
    grant("surgery", "VIEW")
    assert client.get("/api/manual?module=not_a_module").status_code == 400


def test_edit_requires_manage(client, db, grant):
    db.add(ManualSection(module="surgery", slug="overview", title="O",
                        body_md="x", sort_order=10)); db.commit()
    sid = client_get_section_id(client, db, "surgery", "overview")
    grant("surgery", "VIEW")
    assert client.patch(f"/api/manual/{sid}", json={"body_md": "new"}).status_code == 403
    grant("surgery", "MANAGE")
    assert client.patch(f"/api/manual/{sid}", json={"body_md": "new"}).status_code == 200
```

Use the project's existing test fixtures. Look at `backend/tests/test_larc_config_api.py` (or any LARC API test) for how `client`, `db`, and tier-granting are set up; mirror that exact pattern for the `grant(module, tier)` helper (the suite already manipulates a test user's tiers — reuse it; do not invent a new auth mechanism). Replace `client_get_section_id` with a direct `db.query(ManualSection)...one().id` lookup.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_manual_api.py -v`
Expected: FAIL — route 404 (router not mounted yet).

- [ ] **Step 3: Implement the router**

```python
# backend/app/routers/manual.py
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.manual import ManualSection
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier, MODULE_REGISTRY
from app.permissions.resolver import effective_tier

router = APIRouter(prefix="/manual", tags=["manual"])

# module string -> Module enum (only modules that may have a manual)
MODULE_BY_KEY = {m.value: m for m in Module}


def _resolve_module(module: str) -> Module:
    m = MODULE_BY_KEY.get(module)
    if not m:
        raise HTTPException(status_code=400, detail=f"unknown module '{module}'")
    return m


def assert_tier(db: Session, current_user: dict, module: Module, min_tier: Tier):
    email = (current_user.get("email") or "").lower().strip()
    actual = effective_tier(db, email, module)
    if actual < min_tier:
        label = MODULE_REGISTRY[module].label
        raise HTTPException(status_code=403,
                            detail=f"forbidden — needs {min_tier.name.title()} on {label}")


def _dict(s: ManualSection) -> dict:
    return {"id": str(s.id), "slug": s.slug, "title": s.title,
            "body_md": s.body_md, "sort_order": s.sort_order,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "updated_by": s.updated_by}


@router.get("")
def list_sections(module: str = Query(...), db: Session = Depends(get_db),
                  current_user: dict = Depends(get_current_user)):
    m = _resolve_module(module)
    assert_tier(db, current_user, m, Tier.VIEW)
    rows = (db.query(ManualSection).filter_by(module=module)
              .order_by(ManualSection.sort_order, ManualSection.title).all())
    return [_dict(s) for s in rows]


class SectionIn(BaseModel):
    module: str
    slug: str
    title: str
    body_md: str = ""
    sort_order: int = 0


@router.post("", status_code=201)
def create_section(payload: SectionIn, db: Session = Depends(get_db),
                   current_user: dict = Depends(get_current_user)):
    m = _resolve_module(payload.module)
    assert_tier(db, current_user, m, Tier.MANAGE)
    slug = payload.slug.strip().lower().replace(" ", "-")
    if not slug or not payload.title.strip():
        raise HTTPException(status_code=422, detail="slug and title are required")
    if db.query(ManualSection).filter_by(module=payload.module, slug=slug).first():
        raise HTTPException(status_code=409, detail=f"section '{slug}' already exists")
    row = ManualSection(module=payload.module, slug=slug, title=payload.title.strip(),
                        body_md=payload.body_md, sort_order=payload.sort_order,
                        updated_by=current_user.get("email") or "system")
    db.add(row); db.commit(); db.refresh(row)
    return {"id": str(row.id), "slug": row.slug}


class SectionPatch(BaseModel):
    title: Optional[str] = None
    body_md: Optional[str] = None
    sort_order: Optional[int] = None


@router.patch("/{section_id}")
def patch_section(section_id: str, payload: SectionPatch, db: Session = Depends(get_db),
                  current_user: dict = Depends(get_current_user)):
    s = db.query(ManualSection).filter_by(id=section_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="section not found")
    assert_tier(db, current_user, _resolve_module(s.module), Tier.MANAGE)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    s.updated_by = current_user.get("email") or "system"
    db.commit(); db.refresh(s)
    return {"id": str(s.id)}


@router.delete("/{section_id}", status_code=204)
def delete_section(section_id: str, db: Session = Depends(get_db),
                   current_user: dict = Depends(get_current_user)):
    s = db.query(ManualSection).filter_by(id=section_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="section not found")
    assert_tier(db, current_user, _resolve_module(s.module), Tier.MANAGE)
    db.delete(s); db.commit()
```

Mount in `app/main.py` (no blanket `dependencies=` — the handlers self-gate per module). Mirror the existing include style:

```python
from app.routers import manual as manual_router
app.include_router(manual_router.router, prefix="/api")
```

Then delete the old manual CRUD handlers + their Pydantic models from `app/routers/larc.py` (the `/manual` GET/POST/PATCH/DELETE block, ~lines 3695-3790) and the equivalent block in `app/routers/pellet.py`. Leave the rest of those routers intact.

- [ ] **Step 4: Run to verify it passes**

Run:
```bash
cd backend && ./venv/bin/python -m pytest tests/test_manual_api.py -v
./venv/bin/python -c "import app.main"
```
Expected: tests PASS; import OK.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/manual.py backend/app/main.py backend/app/routers/larc.py backend/app/routers/pellet.py backend/tests/test_manual_api.py
git commit -m "feat(manual): unified /api/manual router with per-module tier checks

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Shared `<ModuleManual>` component + staleness badge; migrate LARC/Pellet pages

**Files:**
- Create: `frontend/src/components/manual/ModuleManual.jsx`
- Delete: `frontend/src/pages/LarcManual.jsx`, `frontend/src/pages/PelletManual.jsx`
- Modify: `frontend/src/routes.jsx`
- Test: `npm run build`

- [ ] **Step 1: Build the shared component**

Generalize from the current `LarcManual.jsx` (use it as the source of truth for markup/markdown/edit logic). Key differences:
- Props: `module` (string), `title` (string), `blurb` (string), `backTo` (string, default `'/'`), `backLabel` (string).
- Data: `useQuery({ queryKey: ['manual', module], queryFn: () => api.get('/manual', { params: { module } }).then(r => r.data) })`.
- Create posts `{ module, slug, title, body_md, sort_order }` to `/manual`; patch → `/manual/{id}`; delete → `/manual/{id}`; all invalidate `['manual', module]`.
- Tier for edit: `canEdit = tier(MODULE[moduleEnumKey?], TIER.MANAGE)` — but the component is generic. Simplest: pass `canEdit` decision via the existing `useCurrentUser().tier(...)` using the module string mapped to the frontend `MODULE` value. Since the frontend `MODULE` map values equal the backend strings, call `tier(module, TIER.MANAGE)` directly (the `tier()` helper accepts the module string value).
- **Staleness badge:** at top of file `const MANUAL_STALE_AFTER_DAYS = 180`. For each section compute `stale = section.updated_at && (Date.now() - new Date(section.updated_at)) > MANUAL_STALE_AFTER_DAYS*864e5`. Render under the section title: `Updated {fmt.date(section.updated_at)}` and, when `stale`, an amber pill `Review`. Add the same small `Review` pill next to the section's TOC entry when stale.

Verify against the real `LarcManual.jsx` before writing (it already has TOC + edit/add/delete + `renderMarkdown`); keep all of that, just parameterize the data source + add the badge.

- [ ] **Step 2: Point the existing routes at it; delete old pages**

In `frontend/src/routes.jsx`:
- Replace `import LarcManual from './pages/LarcManual'` and `import PelletManual from './pages/PelletManual'` with `import ModuleManual from './components/manual/ModuleManual'`.
- LARC manual route element → `<ModuleManual module="device_larc" title="LARC Operating Manual" blurb="Working rules for the WWC LARC inventory + tracking workflow." backTo="/larc" backLabel="LARC dashboard" />`.
- Pellet manual route element → `<ModuleManual module="pellets" title="Pellet Operating Manual" blurb="..." backTo="/pellets" backLabel="Pellets" />` (reuse the blurb from the current PelletManual page).
- Delete `frontend/src/pages/LarcManual.jsx` and `frontend/src/pages/PelletManual.jsx`.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: `✓ built` with no errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/components/manual/ModuleManual.jsx frontend/src/routes.jsx
git rm frontend/src/pages/LarcManual.jsx frontend/src/pages/PelletManual.jsx
git commit -m "feat(manual): shared ModuleManual component + staleness badge; migrate LARC/Pellet

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

**Phase 1 checkpoint:** run the LARC + Pellet + manual suites and a build:
`cd backend && ./venv/bin/python -m pytest tests/ -k "manual or larc or pellet" -q` (expect green) and `cd frontend && npm run build`. LARC + Pellet manuals now run on the unified system. This is independently shippable.

---

# Phase 2 — Per-module content + wiring

## Content Task Recipe (applies to every Task 6–14)

Each module task does exactly this:

1. **Author sections.** Read the cited `helpContent.jsx` keys (object literals keyed by the strings listed per task in `frontend/src/components/help/helpContent.jsx`) and skim the cited page files. Distill into the section outline given for that module — **describe only real behavior** (no invention). Match the house style: short, operational markdown; tables for catalogs; numbered steps for workflows; `>` callouts for gotchas. Section bodies typically 300–900 chars, like the LARC/Pellet seeds.
2. **Register the content.** In `backend/app/services/manual_seed.py`, add a `<MODULE>_MANUAL_SECTIONS = [ (slug, title, sort_order, body), ... ]` constant and add `"<module_key>": <MODULE>_MANUAL_SECTIONS` to `MANUAL_SEEDS`.
3. **Add the route + nav link** (frontend), per the module's row in the table below.
4. **Test:** add the module key to the parametrized presence test, run it, and `npm run build`.

**Backend test (extend once, reused by all):** in `backend/tests/test_manual_seed.py`, `test_every_registered_module_seeds_at_least_one` already loops `MANUAL_SEEDS`, so each newly-registered module is covered automatically — just re-run it after each task:
`cd backend && ./venv/bin/python -m pytest tests/test_manual_seed.py -v` (expect PASS, now covering the new module).

**Nav/route wiring:**

| Task | Module key | Has `*Nav.jsx`? | Route to add (in `routes.jsx`) | Where the Manual link goes | helpContent keys to source | Page files to skim |
|---|---|---|---|---|---|---|
| 6 | `surgery` | Yes — `components/surgery/SurgeryNav.jsx` | `{ path: 'manual', element: <ModuleManual module="surgery" title="Surgery Operating Manual" blurb="..." backTo="/surgery" backLabel="Surgery"/>, module: M.SURGERY, tier: TIER.VIEW }` under the `/surgery` group | add a `Manual` `NavLink` to `SurgeryNav` items | surgery-dashboard, surgery-detail, surgery-calendar, surgery-block-schedule, surgery-fee-schedule, surgery-waitlist, surgery-todo, surgery-messages, surgery-payment-posting, surgery-reports, surgery-settings, surgery-bulk-import | `pages/SurgeryDetail.jsx`, `pages/SurgeryBlockSchedule.jsx`, `pages/SurgerySettings.jsx` |
| 7 | `active_ar` | No | add `{ path: '/active-ar/manual', element: <ModuleManual module="active_ar" title="Active AR & Claims Manual" .../>, module: M.ACTIVE_AR, tier: TIER.VIEW }` | add a small `Manual` link (BookOpen + `Link`) in the header of `pages/ActiveAR.jsx` | active-ar, ar-dashboard, claims, denials, appeals, import-files | `pages/ActiveAR.jsx`, `pages/Claims.jsx`, `pages/Denials.jsx`, `pages/Appeals.jsx` |
| 8 | `billing_bank_recon` | No (under `/billing`) | add `manual` child route under the `/billing` group → `<ModuleManual module="billing_bank_recon" .../>` | header link in `pages/Billing.jsx` | bank-recon, billing | `pages/Billing.jsx` |
| 9 | `billing_missing_charges` | No | `{ path: '/billing/missing-charges/manual', element: <ModuleManual module="billing_missing_charges" .../>, module: M.MISSING_CHARGES, tier: TIER.VIEW }` | header link in `pages/MissingCharges.jsx` | missing-charges | `pages/MissingCharges.jsx` |
| 10 | `billing_insurance_docs` | No | `{ path: '/insurance-documents/manual', element: <ModuleManual module="billing_insurance_docs" .../>, module: M.INSURANCE_DOCS, tier: TIER.VIEW }` | header link in `pages/InsuranceDocuments.jsx` | insurance-docs | `pages/InsuranceDocuments.jsx` |
| 11 | `chart` | No | `{ path: '/documents/manual', element: <ModuleManual module="chart" title="Charts & Documents Manual" .../>, module: M.CHART, tier: TIER.VIEW }` | header link in `pages/Documents.jsx` | documents, patients | `pages/Documents.jsx`, `pages/Patients.jsx` |
| 12 | `recall` | Yes — `components/recall/RecallNav.jsx` | `manual` child under `/recalls` → `<ModuleManual module="recall" .../>` | `Manual` NavLink in `RecallNav` | recalls, recall-settings | `pages/` recall pages |
| 13 | `reputation` | Yes — `components/marketing/MarketingNav.jsx` | `manual` child under `/marketing` → `<ModuleManual module="reputation" .../>` | `Manual` NavLink in `MarketingNav` | marketing, marketing-leaderboard, marketing-profiles | marketing pages |
| 14 | `training` | Yes — `components/training/TrainingNav.jsx` | `manual` child under `/training` → `<ModuleManual module="training" .../>` | `Manual` NavLink in `TrainingNav` | training, training-cards | `pages/AdminTrainingCards.jsx`, training pages |

For modules **with** a `*Nav.jsx`: add the Manual link by mirroring how `components/larc/LarcNav.jsx` adds a `NavLink` item (a `{ to: '/<base>/manual', label: 'Manual', tier: TIER.VIEW }` entry in that nav's items list).

For modules **without** a nav bar: add a `Manual` link in the page's existing header row — a `<Link to="...manual" className="...">` with the `BookOpen` icon, styled like other secondary header links on that page. Keep it subtle (top-right of the page title row).

### Tasks 6–14

Each is one module from the table above. **Per task:** follow the Content Task Recipe, then:

- [ ] Author the module's sections (real behavior from the cited sources).
- [ ] Add `<MODULE>_MANUAL_SECTIONS` + register in `MANUAL_SEEDS` (`backend/app/services/manual_seed.py`).
- [ ] Add the route + Manual link per the table row.
- [ ] Run `cd backend && ./venv/bin/python -m pytest tests/test_manual_seed.py -v` (PASS, now covers this module) and `cd frontend && npm run build` (clean).
- [ ] Commit: `git commit -m "feat(manual): <module> operating manual"` (+ co-author trailer).

> **Section outline per module** — author these slugs (sort_order in steps of 10). Use the cited help keys as the content source; expand only to what the code actually does.
> - **Surgery (Task 6):** `overview`, `intake` (new surgery / required fields), `benefits` (verification + patient responsibility), `consent` (BoldSign e-sign), `scheduling` (dates + block calendar), `preop-postop` (the step engine milestones), `statuses` (taxonomy + auto-transitions), `billing` (close-out), `settings`.
> - **Active AR (Task 7):** `overview`, `import` (data import), `claim-queue` (statuses), `era-posting` (payments), `denials`, `appeals`, `views` (Active AR filters/presets).
> - **Bank Recon (Task 8):** `overview`, `workflow` (reconcile + match), `month-close`.
> - **Missing Charges (Task 9):** `overview`, `review` (charge capture), `resolution`.
> - **Insurance Docs (Task 10):** `overview`, `upload` (classify), `retrieval`.
> - **Charts & Documents (Task 11):** `overview`, `chart-lookup`, `documents`, `faxing`, `recalls`.
> - **Recall (Task 12):** `overview`, `lists`, `outreach`, `settings`.
> - **Reputation (Task 13):** `overview`, `reviews`, `leaderboard`, `profiles`.
> - **Training (Task 14):** `overview`, `cards`, `completion`.

Each module task is independently reviewable: spec-compliance review checks the section bodies against the cited `helpContent.jsx` + page behavior (catch any invented behavior), then code-quality review.

---

# Phase 3 — Keep-in-sync convention

### Task 15: Convention memory + house-style note

**Files:**
- Create: a memory file under `/Users/wwcclaudecode/.claude/projects/-Users-wwcclaudecode-Documents-wwc-era-project/memory/` + MEMORY.md pointer.
- Modify: `backend/app/services/manual_seed.py` (top-of-file docstring house-style note).

- [ ] **Step 1: Add the house-style note** to the top of `manual_seed.py`:

```python
"""Per-module manual seed content for the unified manual system.

HOUSE STYLE: short, operational, task-oriented markdown per workflow stage —
tables for catalogs, numbered steps for workflows, `>` callouts for gotchas.
Describe real behavior only.

KEEP IN SYNC: when a module's behavior changes, update that module's manual
section(s) in the same change. The in-app 'Review' badge flags sections older
than MANUAL_STALE_AFTER_DAYS as a backstop.
"""
```

- [ ] **Step 2: Save the convention memory.** Write a `feedback`-type memory file (`feedback_manual_keep_in_sync.md`) capturing: "When a module's behavior changes, update that module's manual section(s) (unified `manual_sections`, seeded in `app/services/manual_seed.py`, edited in-app at `/<module>/manual`). **Why:** manuals drift silently; the staleness badge is only a backstop. **How to apply:** include the manual edit in the same feature change." Add a one-line pointer to `MEMORY.md`.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/manual_seed.py
git commit -m "docs(manual): house-style + keep-in-sync note"
```

---

## Self-Review

**Spec coverage:**
- Unified `manual_sections` table → Task 1. ✓
- Edit-safe migration of LARC/Pellet (preserve edits, idempotent, old tables kept) → Task 2. ✓
- Seed registry, additive-only, LARC/Pellet moved in (incl. today's 2 LARC sections) → Task 3. ✓
- Unified `/api/manual` router, per-module tier (VIEW read / MANAGE edit), unknown module 400, old routers removed → Task 4. ✓
- Shared `<ModuleManual>` component, delete old pages, repoint routes → Task 5. ✓
- Staleness badge (`updated_at` + Review badge, 180-day constant) → Task 5. ✓
- Manuals for the 9 major modules (Surgery, Active AR, Bank Recon, Missing Charges, Insurance Docs, Chart, Recall, Reputation, Training), sourced from helpContent → Tasks 6–14. ✓
- Minor areas (Insurance Contacts, Audit Log, My Checklist) excluded → not in MANUAL_SEEDS. ✓
- Keep-in-sync convention (memory + note) → Task 15. ✓

**Placeholder scan:** Phase-1 tasks carry complete code. Phase-2 bodies are authored-at-implementation from cited real sources (this is content distillation, not a code placeholder) with exact registration/route/test mechanics given. No "TODO"/"TBD".

**Type/name consistency:** `ManualSection` (module, slug, title, body_md, sort_order, created_at, updated_at, updated_by) used consistently across model/migration/seed/router/tests. `MANUAL_SEEDS` dict, `seed_manuals()`, `_migrate_manuals_to_unified()`, `assert_tier`, `MODULE_BY_KEY` consistent. Frontend `['manual', module]` query key, `MANUAL_STALE_AFTER_DAYS` consistent. Module key strings match the verified `Module` enum values.
