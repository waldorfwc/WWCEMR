"""Tests for the unified /api/manual router (Task 4 — consolidated module manuals).

Auth/grant mechanism used here:
- `client` fixture  → TEST_USER (super-admin) always resolves SUPER_ADMIN on every module.
  Used for positive (200/201/204) cases.
- `client_factory`  → build a real User row (NOT super-admin) + seed UserModuleOverride
  rows to set an exact tier per module. Used for 403 gating assertions.
"""
import pytest
from app.models.manual import ManualSection
from app.models.module_tier import UserModuleOverride
from app.models.user import User, UserGroup


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_low_user(db, email="low@waldorfwomenscare.com"):
    """Create a regular (non-super-admin) user with no module grants."""
    u = User(email=email, display_name="Low User", group=UserGroup.CLINICAL,
             is_super_admin=False)
    db.add(u)
    db.commit()
    return u


def _grant(db, user_email, module_value, tier_int):
    """Set (or overwrite) a UserModuleOverride for this user+module."""
    existing = (db.query(UserModuleOverride)
                  .filter_by(user_email=user_email, module=module_value)
                  .first())
    if existing:
        existing.tier = tier_int
    else:
        db.add(UserModuleOverride(
            user_email=user_email,
            module=module_value,
            tier=tier_int,
            added_by="test",
        ))
    db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# GET /api/manual — VIEW gate
# ──────────────────────────────────────────────────────────────────────────────

def test_list_requires_view_denied_with_no_access(client_factory, db):
    """User with NONE tier on surgery → 403."""
    db.add(ManualSection(module="surgery", slug="overview", title="Overview",
                         body_md="body", sort_order=10))
    db.commit()

    u = _make_low_user(db)
    # No override → NONE tier
    c = client_factory(user=u)
    r = c.get("/api/manual?module=surgery")
    assert r.status_code == 403


def test_list_requires_view_granted(client_factory, db):
    """User with VIEW tier on surgery → 200 and the section appears."""
    db.add(ManualSection(module="surgery", slug="overview", title="Overview",
                         body_md="body", sort_order=10))
    db.commit()

    u = _make_low_user(db)
    _grant(db, u.email, "surgery", 10)  # Tier.VIEW = 10
    c = client_factory(user=u)
    r = c.get("/api/manual?module=surgery")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert any(s["slug"] == "overview" for s in data)


def test_list_super_admin_sees_all(client, db):
    """Super-admin client (TEST_USER) always gets 200."""
    db.add(ManualSection(module="device_larc", slug="intro", title="Intro",
                         body_md="", sort_order=0))
    db.commit()
    r = client.get("/api/manual?module=device_larc")
    assert r.status_code == 200
    assert any(s["slug"] == "intro" for s in r.json())


# ──────────────────────────────────────────────────────────────────────────────
# Unknown / invalid module → 400
# ──────────────────────────────────────────────────────────────────────────────

def test_unknown_module_returns_400(client):
    """Unrecognised module value → 400 (not 422 / 404)."""
    r = client.get("/api/manual?module=not_a_real_module")
    assert r.status_code == 400


def test_missing_module_query_param_returns_422(client):
    """Missing required ?module= → 422 from FastAPI validation."""
    r = client.get("/api/manual")
    assert r.status_code == 422


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/manual — MANAGE gate
# ──────────────────────────────────────────────────────────────────────────────

def test_create_requires_manage_denied_with_view(client_factory, db):
    """User with only VIEW tier on pellets → 403 on POST."""
    u = _make_low_user(db)
    _grant(db, u.email, "pellets", 10)  # Tier.VIEW = 10
    c = client_factory(user=u)
    r = c.post("/api/manual", json={"module": "pellets", "slug": "s1",
                                    "title": "S1", "body_md": ""})
    assert r.status_code == 403


def test_create_requires_manage_granted(client_factory, db):
    """User with MANAGE tier on pellets → 201."""
    u = _make_low_user(db)
    _grant(db, u.email, "pellets", 30)  # Tier.MANAGE = 30
    c = client_factory(user=u)
    r = c.post("/api/manual", json={"module": "pellets", "slug": "ops-guide",
                                    "title": "Ops Guide", "body_md": "text"})
    assert r.status_code == 201
    assert r.json()["slug"] == "ops-guide"


def test_create_super_admin(client):
    """Super-admin can create a section."""
    r = client.post("/api/manual", json={"module": "surgery", "slug": "new-sec",
                                         "title": "New Section"})
    assert r.status_code == 201


def test_create_duplicate_slug_409(client, db):
    """Creating a second section with the same module+slug → 409."""
    db.add(ManualSection(module="surgery", slug="dup", title="Dup",
                         body_md="", sort_order=0))
    db.commit()
    r = client.post("/api/manual", json={"module": "surgery", "slug": "dup",
                                         "title": "Dup2"})
    assert r.status_code == 409


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /api/manual/{id} — MANAGE gate
# ──────────────────────────────────────────────────────────────────────────────

def test_patch_requires_manage_denied_with_view(client_factory, db):
    """User with only VIEW tier → 403 on PATCH."""
    s = ManualSection(module="surgery", slug="editable", title="Editable",
                      body_md="", sort_order=0)
    db.add(s); db.commit(); db.refresh(s)
    section_id = str(s.id)

    u = _make_low_user(db)
    _grant(db, u.email, "surgery", 10)  # Tier.VIEW
    c = client_factory(user=u)
    r = c.patch(f"/api/manual/{section_id}", json={"title": "New Title"})
    assert r.status_code == 403


def test_patch_requires_manage_granted(client_factory, db):
    """User with MANAGE tier → 200 on PATCH."""
    s = ManualSection(module="surgery", slug="editable2", title="Old",
                      body_md="", sort_order=0)
    db.add(s); db.commit(); db.refresh(s)
    section_id = str(s.id)

    u = _make_low_user(db)
    _grant(db, u.email, "surgery", 30)  # Tier.MANAGE
    c = client_factory(user=u)
    r = c.patch(f"/api/manual/{section_id}", json={"title": "New Title"})
    assert r.status_code == 200


def test_patch_nonexistent_404(client):
    """Patching an unknown UUID → 404."""
    r = client.patch("/api/manual/00000000-0000-0000-0000-000000000000",
                     json={"title": "x"})
    assert r.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# DELETE /api/manual/{id} — MANAGE gate
# ──────────────────────────────────────────────────────────────────────────────

def test_delete_requires_manage_denied_with_view(client_factory, db):
    """User with only VIEW tier → 403 on DELETE."""
    s = ManualSection(module="device_larc", slug="del-test", title="Del",
                      body_md="", sort_order=0)
    db.add(s); db.commit(); db.refresh(s)
    section_id = str(s.id)

    u = _make_low_user(db)
    _grant(db, u.email, "device_larc", 10)  # VIEW
    c = client_factory(user=u)
    r = c.delete(f"/api/manual/{section_id}")
    assert r.status_code == 403


def test_delete_requires_manage_granted(client_factory, db):
    """User with MANAGE tier → 204 on DELETE."""
    s = ManualSection(module="device_larc", slug="del-test2", title="Del2",
                      body_md="", sort_order=0)
    db.add(s); db.commit(); db.refresh(s)
    section_id = str(s.id)

    u = _make_low_user(db)
    _grant(db, u.email, "device_larc", 30)  # MANAGE
    c = client_factory(user=u)
    r = c.delete(f"/api/manual/{section_id}")
    assert r.status_code == 204


def test_delete_nonexistent_404(client):
    """Deleting an unknown UUID → 404."""
    r = client.delete("/api/manual/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# Cross-module isolation — section stored under module A is invisible from B
# ──────────────────────────────────────────────────────────────────────────────

def test_list_filters_by_module(client, db):
    """GET with module=surgery should not return pellets sections."""
    db.add(ManualSection(module="surgery", slug="surg-sec", title="Surg",
                         body_md="", sort_order=0))
    db.add(ManualSection(module="pellets", slug="pell-sec", title="Pell",
                         body_md="", sort_order=0))
    db.commit()
    r = client.get("/api/manual?module=surgery")
    assert r.status_code == 200
    slugs = {s["slug"] for s in r.json()}
    assert "surg-sec" in slugs
    assert "pell-sec" not in slugs
