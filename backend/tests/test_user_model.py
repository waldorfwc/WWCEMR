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
