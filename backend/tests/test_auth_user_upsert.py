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


def test_get_current_user_returns_clinical_group_for_provisioned_user(db):
    """get_current_user resolves the group of an already-provisioned user.

    CURRENT CONTRACT: get_current_user NO LONGER auto-provisions. A token
    whose subject has no User row now 401s ("Account no longer exists.") —
    accounts are created only in the Google OAuth login flow (auth audit C2).
    So the user row must already exist; here we verify a clinical user
    resolves to group="clinical".
    """
    from app.routers.auth import get_current_user, create_access_token
    from fastapi import Request

    # Pre-seed the clinical user (login flow would have created this row).
    db.add(User(email="brandnew@waldorfwomenscare.com",
                display_name="Brand New", group=UserGroup.CLINICAL))
    db.commit()

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


def test_get_current_user_401s_when_user_row_missing(db):
    """A valid token with no backing User row is rejected, not auto-provisioned.

    Codifies the auth audit C2 fix: a deleted (or never-provisioned) user
    presenting an unexpired JWT can't silently resurrect/create their account.
    """
    from app.routers.auth import get_current_user, create_access_token
    from fastapi import Request
    from fastapi import HTTPException
    import pytest

    token = create_access_token({
        "email": "ghost@waldorfwomenscare.com",
        "name": "Ghost",
    })
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    request = Request(scope)

    with pytest.raises(HTTPException) as exc:
        get_current_user(request, db=db)
    assert exc.value.status_code == 401
    assert "no longer exists" in exc.value.detail.lower()

    # No row was created as a side effect.
    assert db.query(User).filter(
        User.email == "ghost@waldorfwomenscare.com").first() is None


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

    # Pre-seed the user (no auto-provisioning anymore — auth audit C2). The
    # row is stored lowercase, matching the normalized token email.
    db.add(User(email="mixedcase@waldorfwomenscare.com",
                display_name="Mixed", group=UserGroup.CLINICAL))
    db.commit()

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
