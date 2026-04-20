"""Admin user manager — admin-only CRUD on the users table."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User, UserGroup
from app.services.audit_service import log_action
from app.routers.auth import get_current_user

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


class CreateUserPayload(BaseModel):
    email: EmailStr
    group: UserGroup
    display_name: Optional[str] = None


class UpdateUserPayload(BaseModel):
    group: Optional[UserGroup] = None
    display_name: Optional[str] = None


def _sort_key(u: User) -> tuple:
    # admin → billing → clinical, then email asc
    order = {UserGroup.ADMIN: 0, UserGroup.BILLING: 1, UserGroup.CLINICAL: 2}
    return (order.get(u.group, 99), u.email or "")


def _serialize(u: User) -> dict:
    group_val = u.group.value if hasattr(u.group, "value") else u.group
    return {
        "email": u.email,
        "group": group_val,
        "display_name": u.display_name,
        "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
        "updated_at": u.updated_at.isoformat() + "Z" if u.updated_at else None,
    }


@router.get("")
def list_users(db: Session = Depends(get_db)):
    rows = db.query(User).all()
    rows.sort(key=_sort_key)
    return [_serialize(u) for u in rows]


@router.patch("/{email}")
def update_user(
    email: str,
    payload: UpdateUserPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    row = db.query(User).filter(User.email == email).first()
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")

    old = {"group": row.group.value if hasattr(row.group, "value") else row.group,
           "display_name": row.display_name}

    # Last-admin guard
    if payload.group is not None and payload.group != UserGroup.ADMIN and row.group == UserGroup.ADMIN:
        admin_count = db.query(User).filter(User.group == UserGroup.ADMIN).count()
        if admin_count <= 1:
            raise HTTPException(status_code=409, detail="cannot remove the last admin")

    if payload.group is not None:
        row.group = payload.group
    if payload.display_name is not None:
        row.display_name = payload.display_name
    db.commit()
    db.refresh(row)

    new = {"group": row.group.value if hasattr(row.group, "value") else row.group,
           "display_name": row.display_name}
    log_action(db, "USER_UPDATED", "user",
               resource_id=email,
               user_name=current_user.get("email"),
               old_values=old, new_values=new,
               description=f"admin {current_user.get('email')} updated {email}")

    return _serialize(row)
