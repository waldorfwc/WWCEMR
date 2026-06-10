"""Tier-grant + override + super-admin service with audit + last-admin safety.

Higher-level than the resolver — these functions mutate state and write
audit rows. Used by the admin API endpoints (Task 6) and the Phase 2
translation script.
"""
from datetime import datetime
from app.utils.dt import now_utc_naive

from sqlalchemy.orm import Session

from app.models.groups import Group
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import MODULE_REGISTRY, Module, Tier
from app.services.audit_service import log_action


class SuperAdminProtected(Exception):
    """Raised when an action would leave zero Super Admins.
    Always at least one must remain so the system can recover from
    misconfiguration without a DB-level intervention."""


def _tier_label(tier: Tier) -> str:
    return tier.name.replace("_", " ").title()


def set_group_tier(db: Session, *, group_id: str, module: Module,
                    tier: Tier, actor_email: str) -> None:
    """Grant a group a tier on a module. Inserts a new row or updates
    the existing one. Audit row written either way."""
    group = db.query(Group).filter(Group.id == group_id).one()
    row = (db.query(GroupModuleTier)
             .filter_by(group_id=group_id, module=module.value)
             .first())
    before = _tier_label(Tier(row.tier)) if row else "None"
    if row is None:
        row = GroupModuleTier(group_id=group_id, module=module.value,
                               tier=int(tier))
        db.add(row)
    else:
        row.tier = int(tier)
    spec = MODULE_REGISTRY[module]
    log_action(
        db, action="GROUP_PERMS_UPDATED", resource_type="group",
        resource_id=group_id,
        user_id=actor_email, user_name=actor_email,
        description=(f"Set {group.name} → {spec.label} = "
                     f"{_tier_label(tier)} (was {before})"),
    )


def clear_group_tier(db: Session, *, group_id: str, module: Module,
                      actor_email: str) -> None:
    """Remove a group's grant for a module. No-op if the row doesn't exist."""
    group = db.query(Group).filter(Group.id == group_id).one()
    row = (db.query(GroupModuleTier)
             .filter_by(group_id=group_id, module=module.value)
             .first())
    if row is None:
        return
    db.delete(row)
    spec = MODULE_REGISTRY[module]
    log_action(
        db, action="GROUP_PERMS_UPDATED", resource_type="group",
        resource_id=group_id,
        user_id=actor_email, user_name=actor_email,
        description=f"Cleared {group.name} → {spec.label}",
    )


def set_user_override(db: Session, *, user_email: str, module: Module,
                       tier: Tier, actor_email: str) -> None:
    """Set a per-user override on a module. tier=Tier.NONE means
    'explicitly denied — ignore any group grants for this user'."""
    row = (db.query(UserModuleOverride)
             .filter_by(user_email=user_email, module=module.value)
             .first())
    before = _tier_label(Tier(row.tier)) if row else "(none)"
    if row is None:
        row = UserModuleOverride(
            user_email=user_email, module=module.value, tier=int(tier),
            added_by=actor_email, added_at=now_utc_naive(),
        )
        db.add(row)
    else:
        row.tier = int(tier)
        row.added_by = actor_email
        row.added_at = now_utc_naive()
    spec = MODULE_REGISTRY[module]
    log_action(
        db, action="USER_PERMS_OVERRIDE", resource_type="user",
        resource_id=user_email,
        user_id=actor_email, user_name=actor_email,
        description=(f"Override {user_email} → {spec.label} = "
                     f"{_tier_label(tier)} (was {before})"),
    )


def clear_user_override(db: Session, *, user_email: str, module: Module,
                         actor_email: str) -> None:
    """Remove a per-user override. The user's effective tier falls back
    to whatever their group memberships grant."""
    row = (db.query(UserModuleOverride)
             .filter_by(user_email=user_email, module=module.value)
             .first())
    if row is None:
        return
    db.delete(row)
    spec = MODULE_REGISTRY[module]
    log_action(
        db, action="USER_PERMS_OVERRIDE", resource_type="user",
        resource_id=user_email,
        user_id=actor_email, user_name=actor_email,
        description=f"Cleared override {user_email} → {spec.label}",
    )


def set_super_admin(db: Session, *, target_email: str,
                     is_super_admin: bool, actor_email: str) -> None:
    """Grant or revoke the global Super Admin role.

    Enforces last-Super-Admin safety: refuses to demote the only remaining
    Super Admin (would leave zero — the system needs at least one for
    recovery and for granting the Admin tier to others).
    """
    target = db.query(User).filter(User.email == target_email).one()
    if target.is_super_admin and not is_super_admin:
        remaining = (db.query(User)
                       .filter(User.is_super_admin.is_(True),
                               User.email != target_email)
                       .count())
        if remaining == 0:
            raise SuperAdminProtected(
                "Refusing to leave zero Super Admins. "
                "Grant another user Super Admin first.",
            )
    target.is_super_admin = is_super_admin
    action = "SUPER_ADMIN_GRANTED" if is_super_admin else "SUPER_ADMIN_REVOKED"
    log_action(
        db, action=action, resource_type="user", resource_id=target_email,
        user_id=actor_email, user_name=actor_email,
        description=(f"{'Granted' if is_super_admin else 'Revoked'} Super Admin "
                     f"for {target_email}"),
    )
