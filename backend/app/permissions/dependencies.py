"""FastAPI dependency factory: gate routes on a minimum module tier.

Usage:
    # Router-level (everyone on the router needs at least VIEW on Surgery)
    app.include_router(
        surgery.router, prefix="/api",
        dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))],
    )

    # Per-endpoint elevation (write endpoints need WORK; delete needs MANAGE)
    @router.patch("/{id}",
                  dependencies=[Depends(requires_tier(Module.SURGERY, Tier.WORK))])
    def edit_surgery(...): ...
"""
from typing import Callable

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.permissions.catalog import MODULE_REGISTRY, Module, Tier
from app.permissions.resolver import effective_tier
from app.routers.auth import get_current_user


def is_effective_super_admin(user_row) -> bool:
    """Single source of truth for "is this user a Super Admin?"

    Honors EITHER the User.is_super_admin column OR membership in the
    "Super Admin" group. The frontend's /me endpoint and the backend
    requires_super_admin dependency used to disagree: /me treated group
    membership as super-admin while the dependency only checked the
    column, so a user added to the group via the admin UI saw the admin
    UI but got 403 on every backend call. Worst-case, future backend
    code may copy the /me convention and create a real escalation path
    (group membership is editable via admin_groups). One helper, used
    by every callsite, ends that split-brain. (Fable auth audit M4.)
    """
    if user_row is None:
        return False
    if getattr(user_row, "is_super_admin", False):
        return True
    return any(
        (g.name or "").strip().lower() == "super admin"
        for g in (user_row.groups or [])
    )


def requires_super_admin() -> Callable:
    """Return a FastAPI dependency that 403s unless the current user is
    a Super Admin. Use for cross-module sysop endpoints (user lifecycle,
    Google directory sync, etc.) that don't fit any single module."""

    def _dep(
        current_user: dict = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        from app.models.user import User
        email = (current_user.get("email") or "").lower().strip()
        u = db.query(User).filter(User.email == email).first()
        if not is_effective_super_admin(u):
            raise HTTPException(
                status_code=403,
                detail="Super Admin required",
            )
        return current_user

    return _dep


def requires_tier(module: Module, min_tier: Tier) -> Callable:
    """Return a FastAPI dependency that 403s if the current user's
    effective tier on `module` is less than `min_tier`.

    The dependency returns the `current_user` dict unchanged. Handlers
    that need to know the resolved tier later (e.g. to gate UI flags)
    should call `effective_tier(db, email, module)` directly — that's
    the same call this dependency just made, and a cached SQL roundtrip
    in practice. (Fable design review note 7.)
    """

    def _dep(
        current_user: dict = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        email = (current_user.get("email") or "").lower().strip()
        actual = effective_tier(db, email, module)
        if actual < min_tier:
            spec = MODULE_REGISTRY[module]
            tier_label = min_tier.name.replace("_", " ").title()
            actual_label = (actual.name.replace("_", " ").title()
                            if actual != Tier.NONE else "no access")
            raise HTTPException(
                status_code=403,
                detail=(f"forbidden — needs {tier_label} on {spec.label} "
                        f"(you have {actual_label})"),
            )
        return current_user

    return _dep
