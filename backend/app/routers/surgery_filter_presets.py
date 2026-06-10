"""Saved filter presets for the surgery dashboard.

Each scheduler keeps their own named presets ("Awaiting consent under
Dr. Cooke", "Robotic cases at MedStar next 30 days", etc.). A preset is
just a JSON blob of filter params; the frontend pushes them into the
list-query state when loaded.

One preset per user can be marked default — that one gets auto-loaded
when the user lands on the surgery dashboard.
"""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import SurgeryFilterPreset
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier

router = APIRouter(prefix="/surgery-filters", tags=["surgery-filters"])


class FilterPresetIn(BaseModel):
    name: str
    filters_json: dict
    is_default: bool = False


def _to_dict(p: SurgeryFilterPreset) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "filters_json": p.filters_json or {},
        "is_default": bool(p.is_default),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("")
def list_presets(db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    email = current_user.get("email") or ""
    rows = (db.query(SurgeryFilterPreset)
              .filter(SurgeryFilterPreset.owner_email == email)
              .order_by(SurgeryFilterPreset.name).all())
    return [_to_dict(p) for p in rows]


@router.post("")
def create_preset(payload: FilterPresetIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    email = current_user.get("email") or ""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    # Unique name per user
    existing = (db.query(SurgeryFilterPreset)
                  .filter(SurgeryFilterPreset.owner_email == email,
                          SurgeryFilterPreset.name == name)
                  .first())
    if existing:
        # Update in place
        existing.filters_json = payload.filters_json or {}
        existing.is_default = bool(payload.is_default)
        existing.updated_at = now_utc_naive()
        if existing.is_default:
            _clear_other_defaults(db, email, existing.id)
        db.commit(); db.refresh(existing)
        return _to_dict(existing)

    row = SurgeryFilterPreset(
        owner_email=email,
        name=name,
        filters_json=payload.filters_json or {},
        is_default=bool(payload.is_default),
    )
    db.add(row); db.flush()
    if row.is_default:
        _clear_other_defaults(db, email, row.id)
    db.commit(); db.refresh(row)
    return _to_dict(row)


@router.put("/{preset_id}")
def update_preset(preset_id: str, payload: FilterPresetIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    email = current_user.get("email") or ""
    row = (db.query(SurgeryFilterPreset)
             .filter(SurgeryFilterPreset.id == preset_id,
                     SurgeryFilterPreset.owner_email == email)
             .first())
    if not row:
        raise HTTPException(status_code=404, detail="preset not found")
    row.name = payload.name.strip() or row.name
    row.filters_json = payload.filters_json or {}
    row.is_default = bool(payload.is_default)
    row.updated_at = now_utc_naive()
    if row.is_default:
        _clear_other_defaults(db, email, row.id)
    db.commit(); db.refresh(row)
    return _to_dict(row)


@router.delete("/{preset_id}")
def delete_preset(preset_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    email = current_user.get("email") or ""
    row = (db.query(SurgeryFilterPreset)
             .filter(SurgeryFilterPreset.id == preset_id,
                     SurgeryFilterPreset.owner_email == email)
             .first())
    if not row:
        raise HTTPException(status_code=404, detail="preset not found")
    db.delete(row); db.commit()
    return {"ok": True}


def _clear_other_defaults(db: Session, owner_email: str, keep_id) -> None:
    (db.query(SurgeryFilterPreset)
       .filter(SurgeryFilterPreset.owner_email == owner_email,
               SurgeryFilterPreset.id != keep_id,
               SurgeryFilterPreset.is_default.is_(True))
       .update({"is_default": False}, synchronize_session=False))
