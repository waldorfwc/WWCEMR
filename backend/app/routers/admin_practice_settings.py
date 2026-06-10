"""Admin endpoints for practice-wide settings.

Reads/writes the typed PRACTICE_SETTING_REGISTRY (practice address, NPI,
EIN, etc.) used by enrollment-form prefill. Super-Admin only — these are
HIPAA-adjacent identifiers (EIN, Medicaid #, NPI)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.permissions.dependencies import requires_super_admin
from app.routers.auth import get_current_user
from app.services.practice_settings import (
    PRACTICE_SETTING_REGISTRY, REGISTRY_KEYS,
    get_all, set_value,
)

router = APIRouter(prefix="/admin/practice-settings", tags=["admin-practice-settings"])


# Use the shared requires_super_admin() dependency instead of an inline
# `is_super_admin` column check — the inline version missed group-based
# super-admins, so a user added to the "Super Admin" group via
# admin_groups was rejected here but accepted everywhere else. Same
# split-brain Fable auth audit M4 already closed in dependencies.py.


@router.get("")
def list_settings(db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_super_admin())):
    """Return the full registry alongside current values. UI uses both —
    registry drives field order/labels/help, values drive the inputs."""
    values = get_all(db)
    return {
        "settings": [
            {
                "key":   s.key,
                "group": s.group,
                "label": s.label,
                "help":  s.help,
                "value": values.get(s.key),
            }
            for s in PRACTICE_SETTING_REGISTRY
        ],
    }


class UpdateIn(BaseModel):
    value: Optional[str] = None  # empty/None clears


@router.put("/{key}")
def update_setting(key: str, payload: UpdateIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_super_admin())):
    if key not in REGISTRY_KEYS:
        raise HTTPException(status_code=404, detail=f"unknown setting key: {key}")
    actor_email = (current_user.get("email") or "").lower().strip()
    new_val = set_value(db, key, payload.value, actor_email=actor_email)
    return {"ok": True, "key": key, "value": new_val or None}
