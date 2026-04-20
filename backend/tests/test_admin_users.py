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


def test_admin_users_patch_group_success(client, db):
    _seed_three(db)
    r = client.patch("/api/admin/users/b1@waldorfwomenscare.com",
                     json={"group": "admin"})
    assert r.status_code == 200, r.text
    assert r.json()["group"] == "admin"

    row = db.query(User).filter(User.email == "b1@waldorfwomenscare.com").first()
    assert row.group == UserGroup.ADMIN


def test_admin_users_patch_display_name_success(client, db):
    _seed_three(db)
    r = client.patch("/api/admin/users/c1@waldorfwomenscare.com",
                     json={"display_name": "Clinician Updated"})
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Clinician Updated"


def test_admin_users_patch_cannot_remove_last_admin(client, db):
    # Only one admin
    db.add(User(email="only.admin@waldorfwomenscare.com", group=UserGroup.ADMIN))
    db.commit()
    r = client.patch("/api/admin/users/only.admin@waldorfwomenscare.com",
                     json={"group": "billing"})
    assert r.status_code == 409
    assert "last admin" in r.json()["detail"].lower()

    # Row unchanged
    row = db.query(User).filter(User.email == "only.admin@waldorfwomenscare.com").first()
    assert row.group == UserGroup.ADMIN


def test_admin_users_patch_demote_admin_when_another_exists(client, db):
    db.add_all([
        User(email="a1@waldorfwomenscare.com", group=UserGroup.ADMIN),
        User(email="a2@waldorfwomenscare.com", group=UserGroup.ADMIN),
    ])
    db.commit()
    # Two admins — demoting one is allowed
    r = client.patch("/api/admin/users/a1@waldorfwomenscare.com",
                     json={"group": "billing"})
    assert r.status_code == 200


def test_admin_users_patch_404_on_unknown_email(client, db):
    r = client.patch("/api/admin/users/nobody@waldorfwomenscare.com",
                     json={"group": "billing"})
    assert r.status_code == 404


def test_admin_users_patch_forbidden_for_billing(billing_client, db):
    _seed_three(db)
    r = billing_client.patch("/api/admin/users/b1@waldorfwomenscare.com",
                             json={"group": "admin"})
    assert r.status_code == 403
