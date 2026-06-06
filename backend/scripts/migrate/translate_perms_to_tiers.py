"""Phase 2 one-shot — translate legacy verb permissions into tier grants.

For every existing Group:
  1. Read the union of perms held by the group (group_permissions table).
  2. Translate each perm via PERM_TO_TIER → set of (Module, Tier).
  3. For each Module, write the MAX tier into group_module_tiers.
  4. If the group holds any ADMIN_PERMS, additionally grant ADMIN on every
     module (the system-admin role mapping).

For every active User with permissions_extra entries:
  1. Translate each extra perm → list of (Module, Tier).
  2. For each Module, write the MAX tier as a user override IF it would
     exceed what their group memberships already grant.

Also:
  - Ensures the Default Staff group exists with its baseline grants.
  - Auto-joins every active user to Default Staff.

Idempotent (re-running is a no-op). Run via:
    cd backend && python -m scripts.migrate.translate_perms_to_tiers
"""
from collections import defaultdict

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.groups import Group, GroupPermission
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import Module, Tier
from app.permissions.resolver import effective_tier
from app.services.default_staff_group import (
    auto_join_default_staff, ensure_default_staff_group,
)

from .perm_to_tier_map import ADMIN_PERMS, PERM_TO_TIER


def _module_to_max_tier(perms: set[str]) -> dict[Module, int]:
    """Return {module: max_tier} given a set of legacy permission strings."""
    by_module: dict[Module, int] = defaultdict(int)
    for perm in perms:
        for module, tier in PERM_TO_TIER.get(perm, []):
            by_module[module] = max(by_module[module], int(tier))
        if perm in ADMIN_PERMS:
            for m in Module:
                by_module[m] = max(by_module[m], int(Tier.ADMIN))
    return by_module


def translate_group(db: Session, group: Group) -> None:
    """Replace any existing group_module_tiers row for this group with
    one that reflects the union of the group's legacy permissions."""
    perms = {gp.permission for gp in
             db.query(GroupPermission).filter_by(group_id=group.id).all()}
    by_module = _module_to_max_tier(perms)
    for module, tier in by_module.items():
        existing = (db.query(GroupModuleTier)
                      .filter_by(group_id=group.id, module=module.value)
                      .first())
        if existing is None:
            db.add(GroupModuleTier(
                group_id=group.id, module=module.value, tier=tier,
            ))
        elif existing.tier < tier:
            existing.tier = tier
    db.commit()


def translate_user_extras(db: Session, user: User) -> None:
    """Add user overrides for permissions in `permissions_extra` that aren't
    already covered by the user's group memberships."""
    extras = user.permissions_extra or []
    if not extras:
        return
    by_module = _module_to_max_tier(set(extras))
    for module, tier in by_module.items():
        # Don't override if groups already grant at least this tier.
        if int(effective_tier(db, user.email, module)) >= tier:
            continue
        existing = (db.query(UserModuleOverride)
                      .filter_by(user_email=user.email, module=module.value)
                      .first())
        if existing is None:
            db.add(UserModuleOverride(
                user_email=user.email, module=module.value, tier=tier,
                added_by="system:phase2_migration",
            ))
        elif existing.tier < tier:
            existing.tier = tier
    db.commit()


def run(db: Session) -> dict:
    """Translate all groups + active users. Returns a small summary dict
    so callers (script main + tests) can assert on what happened."""
    ensure_default_staff_group(db)
    groups = db.query(Group).all()
    for group in groups:
        translate_group(db, group)
    users = db.query(User).filter_by(is_active=True).all()
    for user in users:
        auto_join_default_staff(db, user.email)
        translate_user_extras(db, user)
    return {"groups": len(groups), "users": len(users)}


def main() -> None:
    db = SessionLocal()
    try:
        summary = run(db)
        print(f"Phase 2 translation complete: "
              f"{summary['groups']} groups, {summary['users']} users.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
