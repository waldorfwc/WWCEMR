"""Admin API for RBAC groups, permissions, and per-user overrides.

All endpoints require the `user:manage` permission (enforced at the router
level via main.py).
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.groups import Group, GroupPermission
from app.models.user import User
from app.routers.auth import get_current_user
from app.services.audit_service import log_action
from app.services.permissions import (
    ALL_PERMISSIONS, PERMISSIONS, effective_permissions,
)


router = APIRouter(prefix="/admin", tags=["admin-rbac"])


# ─── pydantic ────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    permissions: List[str] = []


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class PermissionsReplace(BaseModel):
    permissions: List[str]


class UserGroupsReplace(BaseModel):
    group_ids: List[str]


class UserPermissionsOverride(BaseModel):
    permissions_extra: List[str] = []
    permissions_revoked: List[str] = []


def _group_to_dict(g: Group, with_perms: bool = True,
                    with_members: bool = False) -> dict:
    out = {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "system_protected": g.system_protected,
        "member_count": len(g.members),
        "permission_count": len(g.permissions),
    }
    if with_perms:
        out["permissions"] = sorted(gp.permission for gp in g.permissions)
    if with_members:
        out["members"] = sorted(u.email for u in g.members)
    return out


def _validate_perms(perms: List[str]) -> None:
    bad = [p for p in perms if p not in ALL_PERMISSIONS]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"unknown permission(s): {', '.join(bad)}",
        )


# ─── catalog ─────────────────────────────────────────────────────────

@router.get("/permissions-catalog")
def list_catalog():
    """All permission strings the app recognizes, with descriptions."""
    return {
        "permissions": [
            {"key": k, "description": v}
            for k, v in sorted(PERMISSIONS.items())
        ],
    }


# ─── groups ──────────────────────────────────────────────────────────

@router.get("/groups")
def list_groups(db: Session = Depends(get_db)):
    rows = db.query(Group).order_by(Group.name).all()
    return [_group_to_dict(g, with_perms=False) for g in rows]


@router.get("/groups/{group_id}")
def get_group(group_id: str, db: Session = Depends(get_db)):
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="group not found")
    return _group_to_dict(g, with_perms=True, with_members=True)


@router.post("/groups", status_code=201)
def create_group(payload: GroupCreate, db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if db.query(Group).filter(Group.name == name).first():
        raise HTTPException(status_code=409, detail=f"group '{name}' already exists")
    _validate_perms(payload.permissions)

    g = Group(name=name, description=payload.description, system_protected=False)
    db.add(g); db.flush()
    for p in payload.permissions:
        db.add(GroupPermission(group_id=g.id, permission=p,
                               granted_by=current_user.get("email")))
    db.commit()
    log_action(db, "GROUP_CREATED", "group", resource_id=g.id,
               user_name=current_user.get("email"),
               new_values={"name": name, "permissions": payload.permissions},
               description=f"created group '{name}'")
    db.refresh(g)
    return _group_to_dict(g)


@router.patch("/groups/{group_id}")
def update_group(group_id: str, payload: GroupUpdate,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="group not found")

    old = {"name": g.name, "description": g.description}
    if payload.name is not None:
        new_name = payload.name.strip()
        if new_name and new_name != g.name:
            if db.query(Group).filter(Group.name == new_name,
                                       Group.id != g.id).first():
                raise HTTPException(status_code=409, detail=f"group '{new_name}' already exists")
            g.name = new_name
    if payload.description is not None:
        g.description = payload.description.strip() or None
    db.commit()
    log_action(db, "GROUP_UPDATED", "group", resource_id=g.id,
               user_name=current_user.get("email"),
               old_values=old, new_values={"name": g.name, "description": g.description})
    db.refresh(g)
    return _group_to_dict(g)


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(group_id: str, db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="group not found")
    if g.system_protected:
        raise HTTPException(status_code=409,
                            detail="cannot delete a system-protected group")
    name = g.name
    db.delete(g); db.commit()
    log_action(db, "GROUP_DELETED", "group", resource_id=group_id,
               user_name=current_user.get("email"),
               description=f"deleted group '{name}'")
    return None


@router.put("/groups/{group_id}/permissions")
def replace_group_permissions(group_id: str, payload: PermissionsReplace,
                              db: Session = Depends(get_db),
                              current_user: dict = Depends(get_current_user)):
    """Replace the full permission set on a group. Idempotent."""
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="group not found")
    new_set = set(payload.permissions)
    _validate_perms(list(new_set))

    old_set = {gp.permission for gp in g.permissions}
    # Remove rows no longer in the set
    for gp in list(g.permissions):
        if gp.permission not in new_set:
            db.delete(gp)
    # Add rows for anything new
    for p in new_set - old_set:
        db.add(GroupPermission(group_id=g.id, permission=p,
                               granted_by=current_user.get("email")))
    db.commit()
    log_action(db, "GROUP_PERMS_UPDATED", "group", resource_id=g.id,
               user_name=current_user.get("email"),
               old_values={"permissions": sorted(old_set)},
               new_values={"permissions": sorted(new_set)},
               description=f"updated permissions on group '{g.name}'")
    db.refresh(g)
    return _group_to_dict(g)


# ─── user ↔ group memberships ────────────────────────────────────────

@router.put("/users/{email}/groups")
def replace_user_groups(email: str, payload: UserGroupsReplace,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(get_current_user)):
    """Replace the user's full set of group memberships."""
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    wanted = set(payload.group_ids)
    found = db.query(Group).filter(Group.id.in_(wanted)).all() if wanted else []
    if len(found) != len(wanted):
        missing = wanted - {g.id for g in found}
        raise HTTPException(status_code=422,
                            detail=f"unknown group id(s): {', '.join(sorted(missing))}")

    # Last-Admin guard: if the user is currently in Admin and the new set
    # doesn't include Admin, ensure at least one other Admin remains.
    admin_grp = db.query(Group).filter(Group.name == "Admin").first()
    if admin_grp and admin_grp in user.groups and admin_grp.id not in wanted:
        other_admin_count = sum(1 for u in admin_grp.members if u.email != email)
        if other_admin_count == 0:
            raise HTTPException(status_code=409,
                                detail="cannot remove the last user from the Admin group")

    old_names = sorted(g.name for g in user.groups)
    user.groups = found
    db.commit()
    new_names = sorted(g.name for g in found)
    log_action(db, "USER_GROUPS_UPDATED", "user", resource_id=email,
               user_name=current_user.get("email"),
               old_values={"groups": old_names},
               new_values={"groups": new_names},
               description=f"groups: {old_names} → {new_names}")

    return {"email": email, "groups": new_names,
            "effective_permissions": sorted(effective_permissions(user))}


class AddMemberPayload(BaseModel):
    email: str


@router.post("/groups/{group_id}/members", status_code=201)
def add_group_member(group_id: str, payload: AddMemberPayload,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(get_current_user)):
    """Add a user to a group. Idempotent — returns the group whether the
    user was already a member or just joined."""
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="group not found")
    email = (payload.email or "").lower().strip()
    if not email:
        raise HTTPException(status_code=422, detail="email is required")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    if g in user.groups:
        return _group_to_dict(g, with_perms=True, with_members=True)

    user.groups.append(g)
    db.commit(); db.refresh(g)
    log_action(db, "GROUP_MEMBER_ADDED", "group", resource_id=str(g.id),
               user_name=current_user.get("email"),
               new_values={"email": email, "group": g.name},
               description=f"added {email} to group '{g.name}'")
    return _group_to_dict(g, with_perms=True, with_members=True)


@router.delete("/groups/{group_id}/members/{email}", status_code=204)
def remove_group_member(group_id: str, email: str,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(get_current_user)):
    """Remove a user from a group. Last-Admin guard mirrors the one in
    replace_user_groups: refuses to remove the final user from the Admin
    group."""
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="group not found")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    if g not in user.groups:
        return   # already not a member; nothing to do

    if g.name == "Admin":
        other_admins = [u for u in g.members if u.email != email]
        if not other_admins:
            raise HTTPException(
                status_code=409,
                detail="cannot remove the last user from the Admin group")

    user.groups = [grp for grp in user.groups if grp.id != g.id]
    db.commit()
    log_action(db, "GROUP_MEMBER_REMOVED", "group", resource_id=str(g.id),
               user_name=current_user.get("email"),
               old_values={"email": email, "group": g.name},
               description=f"removed {email} from group '{g.name}'")
    return


@router.put("/users/{email}/permissions-override")
def set_user_permissions_override(
    email: str, payload: UserPermissionsOverride,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Set per-user permissions_extra and permissions_revoked. Replaces both."""
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    _validate_perms(payload.permissions_extra)
    _validate_perms(payload.permissions_revoked)

    # Reject overlap — same perm in both lists is meaningless
    overlap = set(payload.permissions_extra) & set(payload.permissions_revoked)
    if overlap:
        raise HTTPException(status_code=422,
                            detail=f"permission(s) appear in both extras and revoked: {sorted(overlap)}")

    old = {"extras": list(user.permissions_extra or []),
           "revoked": list(user.permissions_revoked or [])}
    user.permissions_extra = sorted(set(payload.permissions_extra)) or None
    user.permissions_revoked = sorted(set(payload.permissions_revoked)) or None
    db.commit()
    new = {"extras": list(user.permissions_extra or []),
           "revoked": list(user.permissions_revoked or [])}
    log_action(db, "USER_PERMS_OVERRIDE", "user", resource_id=email,
               user_name=current_user.get("email"),
               old_values=old, new_values=new,
               description=f"per-user perm override on {email}")

    return {
        "email": email,
        "permissions_extra": new["extras"],
        "permissions_revoked": new["revoked"],
        "effective_permissions": sorted(effective_permissions(user)),
    }


@router.get("/users/{email}/effective-permissions")
def user_effective_permissions(email: str, db: Session = Depends(get_db)):
    """Show another user's effective permissions + breakdown of source.

    Returns: { groups, permissions_extra, permissions_revoked, effective }.
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    eff = sorted(effective_permissions(user))
    by_group = {
        g.name: sorted(gp.permission for gp in g.permissions)
        for g in user.groups
    }
    return {
        "email": email,
        "groups": [{"id": g.id, "name": g.name} for g in user.groups],
        "permissions_by_group": by_group,
        "permissions_extra": list(user.permissions_extra or []),
        "permissions_revoked": list(user.permissions_revoked or []),
        "effective_permissions": eff,
        "permission_descriptions": {
            p: PERMISSIONS[p] for p in eff if p in PERMISSIONS
        },
    }
