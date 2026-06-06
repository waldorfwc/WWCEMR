"""requires_tier dependency: 403 with module-aware message when below tier."""
import pytest
from fastapi import Depends

from app.main import app
from app.models.groups import Group
from app.models.module_tier import GroupModuleTier
from app.models.user import User
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier


# Register a single protected test route once at module load so every
# test in this file shares the same path; the test surface is the gate
# behavior, not the route itself.
@app.get("/__test_protected_surgery_work__",
          dependencies=[Depends(requires_tier(Module.SURGERY, Tier.WORK))])
def _protected():
    return {"ok": True}


def _seed_user_with_group_tier(db, *, email, group_name, tier,
                                module=Module.SURGERY, super_admin=False):
    u = User(email=email, display_name=email.split("@")[0],
             group="CLINICAL", is_active=True, is_super_admin=super_admin)
    g = Group(id=f"g_{group_name.lower().replace(' ', '_')}", name=group_name)
    db.add_all([u, g]); db.flush()
    u.groups.append(g)
    db.add(GroupModuleTier(group_id=g.id, module=module.value, tier=int(tier)))
    db.commit()
    return u


def test_403_when_below_required_tier(client_factory, db):
    u = _seed_user_with_group_tier(
        db, email="x@waldorfwomenscare.com",
        group_name="Front Desk", tier=Tier.VIEW,
    )
    client = client_factory(user=u)
    r = client.get("/__test_protected_surgery_work__")
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "Surgery" in detail
    assert "Work" in detail


def test_200_when_at_required_tier(client_factory, db):
    u = _seed_user_with_group_tier(
        db, email="x@waldorfwomenscare.com",
        group_name="Surgery Coordinators", tier=Tier.WORK,
    )
    client = client_factory(user=u)
    r = client.get("/__test_protected_surgery_work__")
    assert r.status_code == 200


def test_200_when_super_admin(client_factory, db):
    u = User(email="root@waldorfwomenscare.com", display_name="R",
             group="CLINICAL", is_active=True, is_super_admin=True)
    db.add(u); db.commit()
    client = client_factory(user=u)
    r = client.get("/__test_protected_surgery_work__")
    assert r.status_code == 200
