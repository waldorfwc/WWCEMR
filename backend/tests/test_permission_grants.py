"""Grant + override + super-admin service tests."""
import pytest

from app.models.audit import AuditLog
from app.models.groups import Group
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import Module, Tier
from app.services.permission_grants import (
    SuperAdminProtected,
    clear_group_tier,
    clear_user_override,
    set_group_tier,
    set_super_admin,
    set_user_override,
)


def _seed_user(db, email, super_admin=False):
    u = User(email=email, display_name=email, group="CLINICAL",
             is_active=True, is_super_admin=super_admin)
    db.add(u); db.commit()
    return u


def test_set_group_tier_creates_and_audits(db):
    db.add(Group(id="g_b", name="Billing Coders")); db.commit()
    set_group_tier(
        db, group_id="g_b", module=Module.ACTIVE_AR, tier=Tier.WORK,
        actor_email="ocooke@waldorfwomenscare.com",
    )
    row = (db.query(GroupModuleTier)
             .filter_by(group_id="g_b", module="active_ar").one())
    assert row.tier == Tier.WORK
    audit = (db.query(AuditLog)
               .filter_by(action="GROUP_PERMS_UPDATED").first())
    assert audit is not None
    assert "Billing Coders" in audit.description
    assert "Active AR" in audit.description


def test_set_group_tier_updates_existing(db):
    db.add(Group(id="g_b", name="Billing Coders")); db.commit()
    set_group_tier(
        db, group_id="g_b", module=Module.ACTIVE_AR, tier=Tier.WORK,
        actor_email="ocooke@waldorfwomenscare.com",
    )
    set_group_tier(
        db, group_id="g_b", module=Module.ACTIVE_AR, tier=Tier.MANAGE,
        actor_email="ocooke@waldorfwomenscare.com",
    )
    rows = (db.query(GroupModuleTier)
              .filter_by(group_id="g_b", module="active_ar").all())
    assert len(rows) == 1
    assert rows[0].tier == Tier.MANAGE


def test_set_user_override_creates_and_audits(db):
    _seed_user(db, "apetit@waldorfwomenscare.com")
    set_user_override(
        db, user_email="apetit@waldorfwomenscare.com",
        module=Module.ACTIVE_AR, tier=Tier.MANAGE,
        actor_email="ocooke@waldorfwomenscare.com",
    )
    row = (db.query(UserModuleOverride)
             .filter_by(user_email="apetit@waldorfwomenscare.com",
                        module="active_ar").one())
    assert row.tier == Tier.MANAGE
    assert row.added_by == "ocooke@waldorfwomenscare.com"
    audit = (db.query(AuditLog)
               .filter_by(action="USER_PERMS_OVERRIDE").first())
    assert audit is not None


def test_clear_user_override_removes_row(db):
    _seed_user(db, "apetit@waldorfwomenscare.com")
    set_user_override(
        db, user_email="apetit@waldorfwomenscare.com",
        module=Module.ACTIVE_AR, tier=Tier.MANAGE,
        actor_email="ocooke@waldorfwomenscare.com",
    )
    clear_user_override(
        db, user_email="apetit@waldorfwomenscare.com",
        module=Module.ACTIVE_AR,
        actor_email="ocooke@waldorfwomenscare.com",
    )
    assert (db.query(UserModuleOverride)
              .filter_by(user_email="apetit@waldorfwomenscare.com",
                         module="active_ar").first()) is None


def test_set_super_admin_grants(db):
    _seed_user(db, "x@waldorfwomenscare.com")
    set_super_admin(
        db, target_email="x@waldorfwomenscare.com",
        is_super_admin=True, actor_email="root@waldorfwomenscare.com",
    )
    u = db.query(User).filter_by(email="x@waldorfwomenscare.com").one()
    assert u.is_super_admin is True
    audit = (db.query(AuditLog)
               .filter_by(action="SUPER_ADMIN_GRANTED").first())
    assert audit is not None


def test_last_super_admin_cannot_be_demoted(db):
    _seed_user(db, "root@waldorfwomenscare.com", super_admin=True)
    with pytest.raises(SuperAdminProtected):
        set_super_admin(
            db, target_email="root@waldorfwomenscare.com",
            is_super_admin=False, actor_email="root@waldorfwomenscare.com",
        )


def test_demoting_one_of_two_super_admins_succeeds(db):
    _seed_user(db, "root@waldorfwomenscare.com", super_admin=True)
    _seed_user(db, "ocooke@waldorfwomenscare.com", super_admin=True)
    set_super_admin(
        db, target_email="ocooke@waldorfwomenscare.com",
        is_super_admin=False, actor_email="root@waldorfwomenscare.com",
    )
    u = db.query(User).filter_by(email="ocooke@waldorfwomenscare.com").one()
    assert u.is_super_admin is False
