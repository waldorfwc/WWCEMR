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


def test_get_current_user_upserts_new_user_as_clinical(db):
    """Direct call to get_current_user with a request lacking a row creates one."""
    from app.routers.auth import get_current_user, create_access_token
    from fastapi import Request

    # Build a real token for a brand-new user.
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
