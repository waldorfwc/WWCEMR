"""Admin API endpoints for tier grants + Super Admin management.

Auth model:
  - GET endpoints (matrix views)               → Super Admin only
  - PUT /users/{email}/overrides/{module}      → caller needs Admin on
                                                 `module` (Super Admin
                                                 always passes)
  - PUT /users/{email}/super_admin             → caller must be Super Admin
  - PUT /groups/{group}/tiers/{module}         → caller needs Admin on
                                                 `module`

Granting the Admin tier itself is Super-Admin-only (privilege escalation
gate). Per-module Admins can grant View/Work/Manage but not Admin.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.module_tier import GroupModuleTier
from app.models.user import User
from app.permissions.catalog import MODULE_REGISTRY, Module, Tier
from app.permissions.resolver import effective_tier, effective_tier_with_source
from app.routers.auth import get_current_user, normalize_email
from app.services.permission_grants import (
    SuperAdminProtected,
    clear_group_tier,
    clear_user_override,
    set_group_tier,
    set_super_admin,
    set_user_override,
)


router = APIRouter(prefix="/admin", tags=["admin-tiers"])


# ─── Helpers ────────────────────────────────────────────────────────

def _module_or_404(slug: str) -> Module:
    try:
        return Module(slug)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown module: {slug}")


_GRANTABLE_TIERS = (
    Tier.NONE, Tier.VIEW, Tier.WORK, Tier.MANAGE, Tier.ADMIN,
)


def _tier_or_422(name: Optional[str]) -> Tier:
    """Coerce a tier name to a Tier enum, restricted to per-module
    grantable values. SUPER_ADMIN is global-only (see User.is_super_admin)
    and must NEVER be stored on a UserModuleOverride or GroupModuleTier
    row — the previous version of this helper accepted SUPER_ADMIN
    because it's a valid Tier enum member, which let a per-module Admin
    send {"tier":"super_admin"} and store tier 50 on a user override
    that resolved above the ADMIN gate everywhere. (Fable auth audit C1.)
    """
    if name is None:
        # Should be handled by the caller — None means "clear", not a tier.
        raise HTTPException(status_code=422, detail="tier is required")
    upper = "NONE" if name.lower() == "denied" else name.upper()
    try:
        tier = Tier[upper]
    except KeyError:
        raise HTTPException(status_code=422, detail=f"unknown tier: {name}")
    if tier not in _GRANTABLE_TIERS:
        raise HTTPException(
            status_code=422,
            detail=(f"tier {name!r} cannot be granted per-module; "
                    f"allowed: {[t.name.lower() for t in _GRANTABLE_TIERS]}"))
    return tier


def _require_super_admin(current_user: dict, db: Session) -> User:
    email = (current_user.get("email") or "").lower().strip()
    u = db.query(User).filter(User.email == email).first()
    if u is None or not u.is_super_admin:
        raise HTTPException(status_code=403, detail="Super Admin required")
    return u


def _require_admin_on_module(current_user: dict, db: Session,
                              module: Module) -> User:
    email = (current_user.get("email") or "").lower().strip()
    u = db.query(User).filter(User.email == email).first()
    if u is None:
        raise HTTPException(status_code=403, detail="forbidden")
    if u.is_super_admin:
        return u
    if effective_tier(db, email, module) < Tier.ADMIN:
        raise HTTPException(
            status_code=403,
            detail=f"forbidden — needs Admin on {MODULE_REGISTRY[module].label}",
        )
    return u


# ─── GET /users/{email}/tiers ───────────────────────────────────────

@router.get("/users/{email}/tiers")
def get_user_tiers(email: str, db: Session = Depends(get_db),
                    current_user: dict = Depends(get_current_user)):
    _require_super_admin(current_user, db)
    email = normalize_email(email)
    tiers = []
    for module in Module:
        result = effective_tier_with_source(db, email, module)
        tiers.append({
            "module": module.value,
            "label": MODULE_REGISTRY[module].label,
            "tier": result.tier.name.lower(),
            "source_kind": result.source_kind,
            "source_label": result.source_label,
        })
    return {"email": email, "tiers": tiers}


# ─── PUT /users/{email}/overrides/{module} ──────────────────────────

class OverrideIn(BaseModel):
    # "view" | "work" | "manage" | "admin" | "denied" | null (clear)
    tier: Optional[str] = None


@router.put("/users/{email}/overrides/{module_slug}")
def put_user_override(email: str, module_slug: str, payload: OverrideIn,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(get_current_user)):
    module = _module_or_404(module_slug)
    actor = _require_admin_on_module(current_user, db, module)
    # Normalize the path-param email to match how the resolver looks
    # up overrides. Without this, a 'denied' override created for
    # John.Doe@... would never match a lowercased token email.
    # (Fable auth audit H2.)
    email = normalize_email(email)
    # Validate target exists (Fable auth audit M6) — typo'd emails used
    # to create dead overrides silently that would never apply but
    # would still clutter the admin matrix.
    if not db.query(User).filter(User.email == email).first():
        raise HTTPException(
            status_code=404,
            detail=f"No user with email {email!r}")
    if payload.tier is None:
        clear_user_override(db, user_email=email, module=module,
                             actor_email=actor.email)
        return {"ok": True, "cleared": True}
    tier = _tier_or_422(payload.tier)
    # Granting Admin is Super-Admin-only (privilege escalation gate).
    # Use >= so anything at or above ADMIN (including any future
    # near-Super tier) requires Super Admin. The previous == form let a
    # SUPER_ADMIN-typed grant slip past this gate; even with C1's
    # whitelist that closes the immediate hole, >= is the correct
    # semantics. (Fable auth audit C1.)
    if tier >= Tier.ADMIN and not actor.is_super_admin:
        raise HTTPException(
            status_code=403,
            detail="Only Super Admin can grant the Admin tier",
        )
    set_user_override(db, user_email=email, module=module, tier=tier,
                       actor_email=actor.email)
    return {"ok": True, "tier": tier.name.lower()}


# ─── PUT /users/{email}/super_admin ─────────────────────────────────

class SuperAdminIn(BaseModel):
    is_super_admin: bool


@router.put("/users/{email}/super_admin")
def put_super_admin(email: str, payload: SuperAdminIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(get_current_user)):
    actor = _require_super_admin(current_user, db)
    email = normalize_email(email)
    try:
        set_super_admin(db, target_email=email,
                         is_super_admin=payload.is_super_admin,
                         actor_email=actor.email)
    except SuperAdminProtected as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "is_super_admin": payload.is_super_admin}


# ─── GET /groups/{group_id}/tiers ───────────────────────────────────

@router.get("/groups/{group_id}/tiers")
def get_group_tiers(group_id: str, db: Session = Depends(get_db),
                     current_user: dict = Depends(get_current_user)):
    _require_super_admin(current_user, db)
    rows = (db.query(GroupModuleTier)
              .filter(GroupModuleTier.group_id == group_id)
              .all())
    by_module = {r.module: r.tier for r in rows}
    out = []
    for m in Module:
        tier = by_module.get(m.value)
        out.append({
            "module": m.value,
            "label": MODULE_REGISTRY[m].label,
            "tier": Tier(tier).name.lower() if tier is not None else None,
        })
    return {"group_id": group_id, "tiers": out}


# ─── PUT /groups/{group_id}/tiers/{module} ──────────────────────────

class GroupTierIn(BaseModel):
    tier: Optional[str] = None


@router.put("/groups/{group_id}/tiers/{module_slug}")
def put_group_tier(group_id: str, module_slug: str, payload: GroupTierIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(get_current_user)):
    module = _module_or_404(module_slug)
    actor = _require_admin_on_module(current_user, db, module)
    if payload.tier is None:
        clear_group_tier(db, group_id=group_id, module=module,
                         actor_email=actor.email)
        return {"ok": True, "cleared": True}
    tier = _tier_or_422(payload.tier)
    # Use >= so anything at or above ADMIN (including any future
    # near-Super tier) requires Super Admin. The previous == form let a
    # SUPER_ADMIN-typed grant slip past this gate; even with C1's
    # whitelist that closes the immediate hole, >= is the correct
    # semantics. (Fable auth audit C1.)
    if tier >= Tier.ADMIN and not actor.is_super_admin:
        raise HTTPException(
            status_code=403,
            detail="Only Super Admin can grant the Admin tier to a group",
        )
    set_group_tier(db, group_id=group_id, module=module, tier=tier,
                   actor_email=actor.email)
    return {"ok": True, "tier": tier.name.lower()}
