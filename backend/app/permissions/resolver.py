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


def effective_tier_for_users(
    db: Session, user_emails: list[str], module: Module,
) -> dict[str, Tier]:
    """Batch-resolve effective_tier for many users on one module.

    Replaces the N+1 pattern (effective_tier in a Python loop) used by
    list_assignees, checklist.list_templates(include_assignees=true),
    and manager_dashboard. Three queries total instead of 3N.
    (Fable cross-cutting audit #14.)

    Algorithm matches effective_tier_with_source; users not in any
    table resolve to NONE.
    """
    if not user_emails:
        return {}
    emails_lower = [(e or "").strip().lower() for e in user_emails if e]
    # 1. Pull all User rows for super-admin shortcut.
    users = (db.query(User)
                .filter(User.email.in_(emails_lower))
                .all())
    by_email: dict[str, Tier] = {}
    super_admins: set[str] = set()
    for u in users:
        if u.is_super_admin:
            super_admins.add(u.email)
            by_email[u.email] = Tier.SUPER_ADMIN
    pending = [e for e in emails_lower if e not in super_admins]

    if pending:
        # 2. Per-user overrides for the module.
        overrides = (db.query(UserModuleOverride)
                        .filter(UserModuleOverride.user_email.in_(pending),
                                UserModuleOverride.module == module.value)
                        .all())
        for o in overrides:
            by_email[o.user_email] = Tier(o.tier)
        still_pending = [e for e in pending if e not in by_email]

        if still_pending:
            # 3. Best group-grant per remaining user.
            rows = (db.query(user_groups.c.user_email, GroupModuleTier.tier)
                       .join(GroupModuleTier,
                             GroupModuleTier.group_id == user_groups.c.group_id)
                       .filter(user_groups.c.user_email.in_(still_pending),
                               GroupModuleTier.module == module.value)
                       .all())
            best: dict[str, int] = {}
            for email, tier_val in rows:
                if email not in best or tier_val > best[email]:
                    best[email] = tier_val
            for email, tier_val in best.items():
                by_email[email] = Tier(tier_val)

    # Default for users with no entry anywhere.
    for e in emails_lower:
        by_email.setdefault(e, Tier.NONE)
    return by_email


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
