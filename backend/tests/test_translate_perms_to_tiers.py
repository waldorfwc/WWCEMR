"""End-to-end test of the Phase 2 translation script.

Builds fake groups + users with legacy perms, runs the translator, then
checks that the resulting tier rows match what the mapping table says.
"""
from app.models.groups import Group, GroupPermission
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import Module, Tier
from app.permissions.resolver import effective_tier
from app.services.default_staff_group import DEFAULT_STAFF_GROUP_ID

from scripts.migrate.translate_perms_to_tiers import (
    run,
    translate_group,
    translate_user_extras,
)


def test_translate_group_maps_billing_perms_to_active_ar_work(db):
    g = Group(id="g_billing", name="Billing Coders")
    db.add(g); db.flush()
    db.add_all([
        GroupPermission(group_id=g.id, permission="claim:read"),
        GroupPermission(group_id=g.id, permission="claim:edit"),
        GroupPermission(group_id=g.id, permission="payment:post"),
    ])
    db.commit()

    translate_group(db, g)

    row = (db.query(GroupModuleTier)
             .filter_by(group_id=g.id, module="active_ar").one())
    assert row.tier == Tier.WORK


def test_translate_group_max_wins_when_perms_overlap_modules(db):
    g = Group(id="g_manager", name="Billing Manager")
    db.add(g); db.flush()
    db.add_all([
        GroupPermission(group_id=g.id, permission="claim:edit"),       # WORK
        GroupPermission(group_id=g.id, permission="claim:writeoff"),   # MANAGE
    ])
    db.commit()

    translate_group(db, g)

    row = (db.query(GroupModuleTier)
             .filter_by(group_id=g.id, module="active_ar").one())
    assert row.tier == Tier.MANAGE


def test_translate_group_user_manage_grants_admin_on_every_module(db):
    g = Group(id="g_admin", name="System Admin")
    db.add(g); db.flush()
    db.add(GroupPermission(group_id=g.id, permission="user:manage"))
    db.commit()

    translate_group(db, g)

    rows = (db.query(GroupModuleTier)
              .filter_by(group_id=g.id).all())
    by_module = {r.module: r.tier for r in rows}
    for m in Module:
        assert by_module[m.value] == Tier.ADMIN


def test_translate_user_extras_only_adds_override_when_groups_undergrant(db):
    g = Group(id="g_billing", name="Billing Coders")
    u = User(email="apetit@waldorfwomenscare.com", display_name="A",
             group="BILLING", is_active=True,
             permissions_extra=["claim:writeoff"])  # MANAGE
    db.add_all([g, u]); db.flush()
    u.groups.append(g)
    db.add_all([
        GroupPermission(group_id=g.id, permission="claim:read"),  # VIEW
        GroupPermission(group_id=g.id, permission="claim:edit"),  # WORK
    ])
    db.commit()
    # Translate the group first so user check has something to compare to
    translate_group(db, g)

    translate_user_extras(db, u)

    override = (db.query(UserModuleOverride)
                  .filter_by(user_email=u.email, module="active_ar").one())
    assert override.tier == Tier.MANAGE
    # Resolver should now return MANAGE
    assert effective_tier(db, u.email, Module.ACTIVE_AR) == Tier.MANAGE


def test_translate_user_extras_skips_when_groups_already_cover(db):
    g = Group(id="g_billing", name="Billing Coders")
    u = User(email="apetit@waldorfwomenscare.com", display_name="A",
             group="BILLING", is_active=True,
             permissions_extra=["claim:read"])  # VIEW
    db.add_all([g, u]); db.flush()
    u.groups.append(g)
    db.add(GroupPermission(group_id=g.id, permission="claim:writeoff"))  # MANAGE
    db.commit()
    translate_group(db, g)

    translate_user_extras(db, u)

    # No override needed — group already grants more than the extra.
    override = (db.query(UserModuleOverride)
                  .filter_by(user_email=u.email, module="active_ar").first())
    assert override is None


def test_run_seeds_default_staff_and_joins_users(db):
    u = User(email="newhire@waldorfwomenscare.com", display_name="N",
             group="CLINICAL", is_active=True)
    db.add(u); db.commit()

    summary = run(db)

    assert summary["users"] >= 1
    db.refresh(u)
    assert any(g.id == DEFAULT_STAFF_GROUP_ID for g in u.groups)


def test_run_is_idempotent(db):
    g = Group(id="g_billing", name="Billing Coders")
    db.add(g); db.flush()
    db.add(GroupPermission(group_id=g.id, permission="claim:edit"))
    db.commit()

    run(db)
    first_count = db.query(GroupModuleTier).count()
    run(db)
    second_count = db.query(GroupModuleTier).count()

    assert first_count == second_count
