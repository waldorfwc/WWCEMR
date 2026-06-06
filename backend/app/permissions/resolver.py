"""Resolve a user's effective tier on a module.

Algorithm (per spec §Resolution algorithm):
    1. If user.is_super_admin              → SUPER_ADMIN
    2. If override exists for (user, mod)  → override.tier
    3. Else max(group.tier) across groups  → that
    4. Else                                → NONE
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.groups import Group, user_groups
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import Module, Tier


@dataclass
class TierWithSource:
    """Resolved tier plus where it came from.

    source_kind ∈ {"super_admin", "override", "group", "none"}.
    source_label is the specific group name when source_kind == "group".
    """
    tier: Tier
    source_kind: str
    source_label: Optional[str] = None


def effective_tier(db: Session, user_email: str, module: Module) -> Tier:
    return effective_tier_with_source(db, user_email, module).tier


def effective_tier_with_source(
    db: Session, user_email: str, module: Module,
) -> TierWithSource:
    user = db.query(User).filter(User.email == user_email).first()
    if user is None:
        return TierWithSource(Tier.NONE, "none", None)

    if user.is_super_admin:
        return TierWithSource(Tier.SUPER_ADMIN, "super_admin", None)

    override = (db.query(UserModuleOverride)
                  .filter(UserModuleOverride.user_email == user_email,
                          UserModuleOverride.module == module.value)
                  .first())
    if override is not None:
        return TierWithSource(Tier(override.tier), "override", None)

    # Max of group grants. Join through the user_groups secondary table
    # so we can read the specific Group name for the Source column.
    rows = (db.query(GroupModuleTier, Group)
              .join(Group, Group.id == GroupModuleTier.group_id)
              .join(user_groups, user_groups.c.group_id == Group.id)
              .filter(user_groups.c.user_email == user_email,
                      GroupModuleTier.module == module.value)
              .all())
    if not rows:
        return TierWithSource(Tier.NONE, "none", None)
    best_row, best_group = max(rows, key=lambda pair: pair[0].tier)
    return TierWithSource(Tier(best_row.tier), "group", best_group.name)
