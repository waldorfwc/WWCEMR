"""Tests for /api/admin/users endpoints."""
from app.models.user import User, UserGroup


def _seed_three(db):
    db.add_all([
        User(email="a1@waldorfwomenscare.com", group=UserGroup.ADMIN, display_name="A One"),
        User(email="b1@waldorfwomenscare.com", group=UserGroup.BILLING, display_name="B One"),
        User(email="c1@waldorfwomenscare.com", group=UserGroup.CLINICAL, display_name="C One"),
    ])
    db.commit()


def test_admin_users_list_returns_sorted(client, db):
    _seed_three(db)
    r = client.get("/api/admin/users")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    # Sort: admin → billing → clinical, then email asc
    groups_in_order = [row["group"] for row in body]
    # The TEST_USER (admin) is auto-created by upsert in real flows but NOT
    # inserted by the conftest override — so body holds only the 3 seeded rows.
    assert len(body) == 3
    assert groups_in_order == ["admin", "billing", "clinical"]
    assert [row["email"] for row in body] == [
        "a1@waldorfwomenscare.com",
        "b1@waldorfwomenscare.com",
        "c1@waldorfwomenscare.com",
    ]
    assert body[0]["display_name"] == "A One"


def test_admin_users_list_forbidden_for_billing(billing_client, db):
    _seed_three(db)
    assert billing_client.get("/api/admin/users").status_code == 403


def test_admin_users_list_forbidden_for_clinical(clinical_client, db):
    _seed_three(db)
    assert clinical_client.get("/api/admin/users").status_code == 403
