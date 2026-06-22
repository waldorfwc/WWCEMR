"""Regression: the LARC 'Requested By' clinician picklist must be reachable
with LARC Work tier. It used to read /admin/users/clinicians (super-admin
only), which blanked the picker for non-admin staff. Now /larc/clinicians
serves the same list gated on LARC Work.
"""
from app.models.user import User, UserGroup
from app.models.module_tier import UserModuleOverride

LARC = "device_larc"
VIEW, WORK = 10, 20


def _low_user(db, email):
    u = User(email=email, display_name="Low User", group=UserGroup.CLINICAL,
             is_super_admin=False)
    db.add(u); db.commit()
    return u


def _grant(db, user_email, tier_int):
    db.add(UserModuleOverride(user_email=user_email, module=LARC,
                              tier=tier_int, added_by="test"))
    db.commit()


def _clinician(db):
    db.add(User(email="dr@wwc.com", display_name="Dr. Provider",
                group=UserGroup.CLINICAL, is_super_admin=False,
                is_active=True, npi="1234567890", clinician_role="provider",
                credential="MD"))
    db.commit()


def test_larc_clinicians_reachable_with_larc_work(client_factory, db):
    _clinician(db)
    u = _low_user(db, "ma@wwc.com")
    _grant(db, "ma@wwc.com", WORK)
    c = client_factory(user=u)
    r = c.get("/api/larc/clinicians")
    assert r.status_code == 200
    rows = r.json()
    assert any(x["npi"] == "1234567890" and x["clinician_role"] == "provider"
               for x in rows)


def test_larc_clinicians_denied_without_work(client_factory, db):
    _clinician(db)
    u = _low_user(db, "viewer@wwc.com")
    _grant(db, "viewer@wwc.com", VIEW)          # VIEW < WORK
    c = client_factory(user=u)
    assert c.get("/api/larc/clinicians").status_code == 403
