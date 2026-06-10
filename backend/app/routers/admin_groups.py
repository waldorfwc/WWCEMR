"""Admin API for RBAC groups + memberships.

Tier grants live elsewhere — see app/routers/admin_tiers.py for the
per-module tier grid endpoints. This router only handles the Group
records themselves (name/description/system_protected) and the
user↔group membership table.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func as _sa_func
from sqlalchemy.orm import Session

func_lower = _sa_func.lower

from app.database import get_db
from app.models.groups import Group
from app.models.user import User
from app.routers.auth import get_current_user, normalize_email
from app.services.audit_service import log_action


router = APIRouter(prefix="/admin", tags=["admin-rbac"])


# ─── pydantic ────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class UserGroupsReplace(BaseModel):
    group_ids: List[str]


def _group_to_dict(g: Group, with_members: bool = False) -> dict:
    out = {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "system_protected": g.system_protected,
        "member_count": len(g.members),
    }
    if with_members:
        out["members"] = sorted(u.email for u in g.members)
    return out


# ─── groups ──────────────────────────────────────────────────────────

@router.get("/groups")
def list_groups(db: Session = Depends(get_db)):
    rows = db.query(Group).order_by(Group.name).all()
    return [_group_to_dict(g) for g in rows]


@router.get("/groups/{group_id}")
def get_group(group_id: str, db: Session = Depends(get_db)):
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="group not found")
    return _group_to_dict(g, with_members=True)


@router.post("/groups", status_code=201)
def create_group(payload: GroupCreate, db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if db.query(Group).filter(Group.name == name).first():
        raise HTTPException(status_code=409, detail=f"group '{name}' already exists")

    g = Group(name=name, description=payload.description, system_protected=False)
    db.add(g); db.commit(); db.refresh(g)
    log_action(db, "GROUP_CREATED", "group", resource_id=g.id,
               user_name=current_user.get("email"),
               new_values={"name": name},
               description=(f"created group '{name}' (use the Tiers grid to "
                            f"grant per-module access)"))
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


# Group permission grants now live in app/routers/admin_tiers.py
# (PUT /api/admin/groups/{id}/tiers/{module} — replaces the old
# PUT /groups/{id}/permissions).


# ─── user ↔ group memberships ────────────────────────────────────────

@router.get("/users/{email}/groups")
def get_user_groups(email: str, db: Session = Depends(get_db)):
    """Return the user's group memberships. Used by the per-user group
    editor in Admin.jsx."""
    email = normalize_email(email)
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return {
        "email": email,
        "groups": [{"id": g.id, "name": g.name} for g in user.groups],
    }


@router.put("/users/{email}/groups")
def replace_user_groups(email: str, payload: UserGroupsReplace,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(get_current_user)):
    """Replace the user's full set of group memberships."""
    email = normalize_email(email)
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    wanted = set(payload.group_ids)
    found = db.query(Group).filter(Group.id.in_(wanted)).all() if wanted else []
    if len(found) != len(wanted):
        missing = wanted - {g.id for g in found}
        raise HTTPException(status_code=422,
                            detail=f"unknown group id(s): {', '.join(sorted(missing))}")

    # Last-Super-Admin guard: per M4 the canonical privilege is
    # is_super_admin OR membership in the "Super Admin" group. If the
    # user is currently in "Super Admin" and the new set drops it,
    # ensure the system still has at least one other effective
    # Super Admin (column-flagged user OR another "Super Admin" group
    # member). (Fable auth audit M2.)
    super_grp = (db.query(Group)
                   .filter(func_lower(Group.name) == "super admin").first())
    if super_grp and super_grp in user.groups and super_grp.id not in wanted:
        # Count other Super Admins via column OR via group membership.
        col_others = (db.query(User)
                        .filter(User.is_super_admin.is_(True),
                                User.email != email)
                        .count())
        grp_others = sum(
            1 for u in super_grp.members
            if normalize_email(u.email) != email)
        if col_others + grp_others == 0:
            raise HTTPException(
                status_code=409,
                detail=("cannot remove the last Super Admin — promote "
                        "another user first"))

    old_names = sorted(g.name for g in user.groups)
    user.groups = found
    db.commit()
    new_names = sorted(g.name for g in found)
    log_action(db, "USER_GROUPS_UPDATED", "user", resource_id=email,
               user_name=current_user.get("email"),
               old_values={"groups": old_names},
               new_values={"groups": new_names},
               description=f"groups: {old_names} → {new_names}")

    return {"email": email, "groups": new_names}


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
    email = normalize_email(payload.email)
    if not email:
        raise HTTPException(status_code=422, detail="email is required")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    if g in user.groups:
        return _group_to_dict(g, with_members=True)

    user.groups.append(g)
    db.commit(); db.refresh(g)
    log_action(db, "GROUP_MEMBER_ADDED", "group", resource_id=str(g.id),
               user_name=current_user.get("email"),
               new_values={"email": email, "group": g.name},
               description=f"added {email} to group '{g.name}'")
    return _group_to_dict(g, with_members=True)


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
    email = normalize_email(email)
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    if g not in user.groups:
        return   # already not a member; nothing to do

    if (g.name or "").strip().lower() == "super admin":
        # Last-Super-Admin guard via the canonical authority
        # (Fable auth audit M2). Count via column OR group membership.
        col_others = (db.query(User)
                        .filter(User.is_super_admin.is_(True),
                                User.email != email)
                        .count())
        grp_others = [u for u in g.members
                       if normalize_email(u.email) != email]
        if col_others == 0 and not grp_others:
            raise HTTPException(
                status_code=409,
                detail=("cannot remove the last Super Admin — promote "
                        "another user first"))
    if g.name == "Admin":
        other_admins = [u for u in g.members
                         if normalize_email(u.email) != email]
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


# Per-user permission overrides now live in app/routers/admin_tiers.py
# (PUT /api/admin/users/{email}/overrides/{module}).
# The legacy permissions_extra / permissions_revoked columns are dropped
# in the same commit that removes this file's legacy endpoints.
