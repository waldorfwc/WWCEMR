"""Schema tests for the new tier tables and is_super_admin column."""
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User


def test_group_module_tier_round_trip(db):
    row = GroupModuleTier(group_id="g_billing", module="active_ar", tier=20)
    db.add(row)
    db.commit()
    fetched = (db.query(GroupModuleTier)
                         .filter_by(group_id="g_billing", module="active_ar")
                         .one())
    assert fetched.tier == 20


def test_user_override_round_trip(db):
    row = UserModuleOverride(
        user_email="apetit@waldorfwomenscare.com",
        module="active_ar", tier=30,
        added_by="ocooke@waldorfwomenscare.com",
    )
    db.add(row)
    db.commit()
    fetched = (db.query(UserModuleOverride)
                         .filter_by(user_email="apetit@waldorfwomenscare.com",
                                    module="active_ar")
                         .one())
    assert fetched.tier == 30
    assert fetched.added_by == "ocooke@waldorfwomenscare.com"


def test_super_admin_column_default_false(db):
    u = User(email="x@waldorfwomenscare.com", display_name="X",
             group="CLINICAL", is_active=True)
    db.add(u); db.commit()
    assert u.is_super_admin is False
