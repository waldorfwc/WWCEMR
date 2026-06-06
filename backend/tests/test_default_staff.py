"""Default Staff group: seed + auto-join hook for new users."""
from app.models.groups import Group
from app.models.module_tier import GroupModuleTier
from app.models.user import User
from app.permissions.catalog import Module, Tier
from app.services.default_staff_group import (
    DEFAULT_STAFF_GROUP_ID,
    auto_join_default_staff,
    ensure_default_staff_group,
)


def test_ensure_creates_group_with_baseline_grants(db):
    ensure_default_staff_group(db)
    g = db.query(Group).filter_by(id=DEFAULT_STAFF_GROUP_ID).one()
    assert g.name == "Default Staff"
    tiers = {r.module: r.tier for r in
             db.query(GroupModuleTier).filter_by(group_id=g.id).all()}
    assert tiers == {
        Module.CHART.value: int(Tier.VIEW),
        Module.MY_CHECKLIST.value: int(Tier.WORK),
    }


def test_ensure_is_idempotent(db):
    ensure_default_staff_group(db)
    ensure_default_staff_group(db)
    g_count = db.query(Group).filter_by(id=DEFAULT_STAFF_GROUP_ID).count()
    tier_count = (db.query(GroupModuleTier)
                    .filter_by(group_id=DEFAULT_STAFF_GROUP_ID).count())
    assert g_count == 1
    assert tier_count == 2


def test_auto_join_adds_membership(db):
    ensure_default_staff_group(db)
    u = User(email="newhire@waldorfwomenscare.com",
             display_name="New Hire", group="CLINICAL", is_active=True)
    db.add(u); db.commit()
    auto_join_default_staff(db, u.email)
    db.refresh(u)
    assert any(g.id == DEFAULT_STAFF_GROUP_ID for g in u.groups)


def test_auto_join_is_idempotent(db):
    ensure_default_staff_group(db)
    u = User(email="newhire@waldorfwomenscare.com",
             display_name="New Hire", group="CLINICAL", is_active=True)
    db.add(u); db.commit()
    auto_join_default_staff(db, u.email)
    auto_join_default_staff(db, u.email)
    db.refresh(u)
    matches = [g for g in u.groups if g.id == DEFAULT_STAFF_GROUP_ID]
    assert len(matches) == 1
