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
    # Sort: admin → billing → clinical, then email asc.
    # The `client` fixture now seeds TEST_USER (admin, tester@…) as a real
    # super-admin row so per-module tier lookups resolve — so the list holds
    # 4 rows: the 3 seeded here plus TEST_USER. TEST_USER sorts among the
    # admins by email: "a1@…" < "tester@…", so order is a1, tester, b1, c1.
    groups_in_order = [row["group"] for row in body]
    assert len(body) == 4
    assert groups_in_order == ["admin", "admin", "billing", "clinical"]
    assert [row["email"] for row in body] == [
        "a1@waldorfwomenscare.com",
        "tester@waldorfwomenscare.com",
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


def test_admin_users_patch_group_does_not_gate_last_admin(client, db):
    # CURRENT CONTRACT: the legacy `group` column no longer drives privilege
    # (authority is User.is_super_admin + per-module tiers), so PATCHing the
    # only legacy-admin's group is allowed and does NOT raise a last-admin
    # 409. The last-Super-Admin guard now lives on set_super_admin and
    # delete_user, which key on is_super_admin — not on this endpoint.
    db.add(User(email="only.admin@waldorfwomenscare.com", group=UserGroup.ADMIN))
    db.commit()
    r = client.patch("/api/admin/users/only.admin@waldorfwomenscare.com",
                     json={"group": "billing"})
    assert r.status_code == 200, r.text
    assert r.json()["group"] == "billing"

    # Row was demoted — the group field carries no authority, so this is safe.
    row = db.query(User).filter(User.email == "only.admin@waldorfwomenscare.com").first()
    assert row.group == UserGroup.BILLING


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


def test_admin_users_post_creates(client, db):
    r = client.post("/api/admin/users",
                    json={"email": "new@waldorfwomenscare.com",
                          "group": "billing",
                          "display_name": "New Hire"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "new@waldorfwomenscare.com"
    assert body["group"] == "billing"
    assert body["display_name"] == "New Hire"

    row = db.query(User).filter(User.email == "new@waldorfwomenscare.com").first()
    assert row is not None
    assert row.group == UserGroup.BILLING


def test_admin_users_post_lowercases_and_strips_email(client, db):
    r = client.post("/api/admin/users",
                    json={"email": "  MixedCase@waldorfwomenscare.com  ",
                          "group": "clinical"})
    assert r.status_code == 201
    assert r.json()["email"] == "mixedcase@waldorfwomenscare.com"
    row = db.query(User).filter(User.email == "mixedcase@waldorfwomenscare.com").first()
    assert row is not None


def test_admin_users_post_duplicate_email(client, db):
    db.add(User(email="dup@waldorfwomenscare.com", group=UserGroup.BILLING))
    db.commit()
    r = client.post("/api/admin/users",
                    json={"email": "dup@waldorfwomenscare.com",
                          "group": "billing"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"].lower()


def test_admin_users_post_invalid_group(client, db):
    r = client.post("/api/admin/users",
                    json={"email": "bad@waldorfwomenscare.com",
                          "group": "superuser"})
    assert r.status_code == 422


def test_admin_users_post_forbidden_for_clinical(clinical_client, db):
    r = clinical_client.post("/api/admin/users",
                             json={"email": "x@waldorfwomenscare.com",
                                   "group": "billing"})
    assert r.status_code == 403
