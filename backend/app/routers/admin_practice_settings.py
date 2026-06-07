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
from app.routers.auth import get_current_user
from app.services.practice_settings import (
    PRACTICE_SETTING_REGISTRY, REGISTRY_KEYS,
    get_all, set_value,
)

router = APIRouter(prefix="/admin/practice-settings", tags=["admin-practice-settings"])


def _require_super_admin(current_user: dict, db: Session) -> User:
    email = (current_user.get("email") or "").lower().strip()
    u = db.query(User).filter(User.email == email).first()
    if u is None or not u.is_super_admin:
        raise HTTPException(status_code=403, detail="Super Admin required")
    return u


@router.get("")
def list_settings(db: Session = Depends(get_db),
                  current_user: dict = Depends(get_current_user)):
    """Return the full registry alongside current values. UI uses both —
    registry drives field order/labels/help, values drive the inputs."""
    _require_super_admin(current_user, db)
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
                   current_user: dict = Depends(get_current_user)):
    actor = _require_super_admin(current_user, db)
    if key not in REGISTRY_KEYS:
        raise HTTPException(status_code=404, detail=f"unknown setting key: {key}")
    new_val = set_value(db, key, payload.value, actor_email=actor.email)
    return {"ok": True, "key": key, "value": new_val or None}
