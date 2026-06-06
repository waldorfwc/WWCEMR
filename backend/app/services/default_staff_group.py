"""Default Staff group — system-managed, auto-joined by every new user.

Baseline grants (per the design spec):
  - Chart        → View
  - My Checklist → Work

Admin can edit the group's tiers via the standard admin tier API; future
hires inherit whatever's set at the time of their first sign-in.
"""
from sqlalchemy.orm import Session

from app.models.groups import Group
from app.models.module_tier import GroupModuleTier
from app.models.user import User
from app.permissions.catalog import Module, Tier


DEFAULT_STAFF_GROUP_ID   = "default_staff"
DEFAULT_STAFF_GROUP_NAME = "Default Staff"

DEFAULT_STAFF_GRANTS: dict[Module, Tier] = {
    Module.CHART:        Tier.VIEW,
    Module.MY_CHECKLIST: Tier.WORK,
}


def ensure_default_staff_group(db: Session) -> None:
    """Idempotently create the Default Staff group + its baseline grants."""
    g = db.query(Group).filter_by(id=DEFAULT_STAFF_GROUP_ID).first()
    if g is None:
        g = Group(
            id=DEFAULT_STAFF_GROUP_ID,
            name=DEFAULT_STAFF_GROUP_NAME,
            system_protected=True,  # block UI deletion
        )
        db.add(g)
        db.flush()
    for module, tier in DEFAULT_STAFF_GRANTS.items():
        existing = (db.query(GroupModuleTier)
                      .filter_by(group_id=g.id, module=module.value)
                      .first())
        if existing is None:
            db.add(GroupModuleTier(
                group_id=g.id, module=module.value, tier=int(tier),
            ))
        elif existing.tier != int(tier):
            existing.tier = int(tier)
    db.commit()


def auto_join_default_staff(db: Session, user_email: str) -> None:
    """Add user to Default Staff group (idempotent). Caller is responsible
    for ensuring the group exists first; safe to call ensure_default_staff_group
    just before this in a per-request hook."""
    user = db.query(User).filter(User.email == user_email).first()
    if user is None:
        return
    if any(g.id == DEFAULT_STAFF_GROUP_ID for g in user.groups):
        return
    group = db.query(Group).filter_by(id=DEFAULT_STAFF_GROUP_ID).first()
    if group is None:
        return
    user.groups.append(group)
    db.commit()
