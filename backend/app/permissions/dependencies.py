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
        if u is None or not u.is_super_admin:
            raise HTTPException(
                status_code=403,
                detail="Super Admin required",
            )
        return current_user

    return _dep


def requires_tier(module: Module, min_tier: Tier) -> Callable:
    """Return a FastAPI dependency that 403s if the current user's
    effective tier on `module` is less than `min_tier`."""

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
        # Inject the resolved tier so handlers can branch on it without
        # re-querying.
        out = dict(current_user)
        out.setdefault("module_tier", {})[module.value] = int(actual)
        return out

    return _dep
