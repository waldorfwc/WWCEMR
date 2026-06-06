"""Admin tier API: read resolved tiers, set/clear overrides, super-admin."""
from app.models.groups import Group
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import Tier


def _seed_super_admin(db, email="root@waldorfwomenscare.com"):
    u = User(email=email, display_name="Root", group="CLINICAL",
             is_active=True, is_super_admin=True)
    db.add(u); db.commit()
    return u


def test_get_user_tiers_returns_resolved_grid(client_factory, db):
    u = User(email="apetit@waldorfwomenscare.com", display_name="A",
             group="BILLING", is_active=True)
    g = Group(id="g_b", name="Billing Coders")
    db.add_all([u, g]); db.flush()
    u.groups.append(g)
    db.add(GroupModuleTier(group_id=g.id, module="active_ar", tier=Tier.WORK))
    db.commit()
    root = _seed_super_admin(db)
    client = client_factory(user=root)
    r = client.get(f"/api/admin/users/{u.email}/tiers")
    assert r.status_code == 200, r.text
    body = r.json()
    entries = {e["module"]: e for e in body["tiers"]}
    assert entries["active_ar"]["tier"] == "work"
    assert entries["active_ar"]["source_kind"] == "group"
    assert entries["active_ar"]["source_label"] == "Billing Coders"
    # Modules with no grant come back as "none" / source "none"
    assert entries["surgery"]["tier"] == "none"


def test_put_user_override_creates(client_factory, db):
    u = User(email="apetit@waldorfwomenscare.com", display_name="A",
             group="BILLING", is_active=True)
    db.add(u); db.commit()
    root = _seed_super_admin(db)
    client = client_factory(user=root)
    r = client.put(
        f"/api/admin/users/{u.email}/overrides/active_ar",
        json={"tier": "manage"},
    )
    assert r.status_code == 200, r.text
    row = (db.query(UserModuleOverride)
             .filter_by(user_email=u.email, module="active_ar").one())
    assert row.tier == Tier.MANAGE


def test_put_user_override_with_null_clears(client_factory, db):
    u = User(email="apetit@waldorfwomenscare.com", display_name="A",
             group="BILLING", is_active=True)
    db.add(u); db.commit()
    db.add(UserModuleOverride(
        user_email=u.email, module="active_ar", tier=Tier.MANAGE,
        added_by="root@waldorfwomenscare.com",
    ))
    db.commit()
    root = _seed_super_admin(db)
    client = client_factory(user=root)
    r = client.put(
        f"/api/admin/users/{u.email}/overrides/active_ar",
        json={"tier": None},
    )
    assert r.status_code == 200, r.text
    assert (db.query(UserModuleOverride)
              .filter_by(user_email=u.email, module="active_ar")
              .first()) is None


def test_put_super_admin_requires_super_admin(client_factory, db):
    target = User(email="t@waldorfwomenscare.com", display_name="T",
                  group="CLINICAL", is_active=True)
    non_root = User(email="x@waldorfwomenscare.com", display_name="X",
                    group="CLINICAL", is_active=True, is_super_admin=False)
    db.add_all([target, non_root]); db.commit()
    client = client_factory(user=non_root)
    r = client.put(
        f"/api/admin/users/{target.email}/super_admin",
        json={"is_super_admin": True},
    )
    assert r.status_code == 403


def test_put_super_admin_works_for_super_admin(client_factory, db):
    target = User(email="t@waldorfwomenscare.com", display_name="T",
                  group="CLINICAL", is_active=True)
    db.add(target); db.commit()
    root = _seed_super_admin(db)
    client = client_factory(user=root)
    r = client.put(
        f"/api/admin/users/{target.email}/super_admin",
        json={"is_super_admin": True},
    )
    assert r.status_code == 200, r.text
    db.refresh(target)
    assert target.is_super_admin is True


def test_last_super_admin_demote_returns_409(client_factory, db):
    root = _seed_super_admin(db)
    client = client_factory(user=root)
    r = client.put(
        f"/api/admin/users/{root.email}/super_admin",
        json={"is_super_admin": False},
    )
    assert r.status_code == 409
    body = r.json()
    assert "Super Admin" in body["detail"]


def test_put_group_tier_requires_admin_on_module(client_factory, db):
    g = Group(id="g_b", name="Billing Coders")
    actor = User(email="actor@waldorfwomenscare.com", display_name="Act",
                 group="BILLING", is_active=True)
    db.add_all([g, actor]); db.commit()
    client = client_factory(user=actor)
    r = client.put(
        f"/api/admin/groups/{g.id}/tiers/active_ar",
        json={"tier": "manage"},
    )
    assert r.status_code == 403


def test_put_group_tier_works_with_super_admin(client_factory, db):
    g = Group(id="g_b", name="Billing Coders")
    db.add(g); db.commit()
    root = _seed_super_admin(db)
    client = client_factory(user=root)
    r = client.put(
        f"/api/admin/groups/{g.id}/tiers/active_ar",
        json={"tier": "work"},
    )
    assert r.status_code == 200, r.text
    row = (db.query(GroupModuleTier)
             .filter_by(group_id=g.id, module="active_ar").one())
    assert row.tier == Tier.WORK
