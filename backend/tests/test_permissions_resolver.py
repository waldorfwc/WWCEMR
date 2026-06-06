"""Resolver tests: override > max-of-groups > none, plus super_admin short-circuit."""
import pytest

from app.models.groups import Group
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import Module, Tier
from app.permissions.resolver import effective_tier, effective_tier_with_source


@pytest.fixture
def user_with_groups(db):
    u = User(email="apetit@waldorfwomenscare.com", display_name="Apetit",
             group="BILLING", is_active=True)
    g1 = Group(id="g_billing", name="Billing Coders")
    g2 = Group(id="g_frontdesk", name="Front Desk")
    db.add_all([u, g1, g2])
    db.flush()
    u.groups.append(g1)
    u.groups.append(g2)
    db.add_all([
        GroupModuleTier(group_id="g_billing", module="active_ar", tier=Tier.WORK),
        GroupModuleTier(group_id="g_frontdesk", module="active_ar", tier=Tier.VIEW),
    ])
    db.commit()
    return u


def test_max_of_groups_wins(db, user_with_groups):
    # billing=WORK, frontdesk=VIEW → effective WORK
    t = effective_tier(db, user_with_groups.email, Module.ACTIVE_AR)
    assert t == Tier.WORK


def test_override_beats_group(db, user_with_groups):
    db.add(UserModuleOverride(
        user_email=user_with_groups.email,
        module="active_ar", tier=Tier.MANAGE,
        added_by="ocooke@waldorfwomenscare.com",
    ))
    db.commit()
    t = effective_tier(db, user_with_groups.email, Module.ACTIVE_AR)
    assert t == Tier.MANAGE


def test_denied_override_blocks_group_grant(db, user_with_groups):
    db.add(UserModuleOverride(
        user_email=user_with_groups.email,
        module="active_ar", tier=Tier.NONE,
        added_by="ocooke@waldorfwomenscare.com",
    ))
    db.commit()
    t = effective_tier(db, user_with_groups.email, Module.ACTIVE_AR)
    assert t == Tier.NONE


def test_super_admin_short_circuits_to_max(db, user_with_groups):
    user_with_groups.is_super_admin = True
    db.commit()
    t = effective_tier(db, user_with_groups.email, Module.SURGERY)
    assert t == Tier.SUPER_ADMIN


def test_no_grant_returns_none(db):
    u = User(email="nobody@waldorfwomenscare.com", display_name="N",
             group="CLINICAL", is_active=True)
    db.add(u); db.commit()
    t = effective_tier(db, u.email, Module.ACTIVE_AR)
    assert t == Tier.NONE


def test_source_reports_specific_group_name(db, user_with_groups):
    result = effective_tier_with_source(
        db, user_with_groups.email, Module.ACTIVE_AR,
    )
    assert result.tier == Tier.WORK
    assert result.source_kind == "group"
    assert result.source_label == "Billing Coders"


def test_source_reports_override(db, user_with_groups):
    db.add(UserModuleOverride(
        user_email=user_with_groups.email,
        module="active_ar", tier=Tier.MANAGE,
        added_by="ocooke@waldorfwomenscare.com",
    ))
    db.commit()
    result = effective_tier_with_source(
        db, user_with_groups.email, Module.ACTIVE_AR,
    )
    assert result.tier == Tier.MANAGE
    assert result.source_kind == "override"
