"""Surgery module config + admin endpoints (Phase B).

Permissions:
  GET picklist endpoints                          claim:read
  All admin endpoints (POST/PUT/PATCH/DELETE)     user:manage
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery_config import (
    SurgeryConfig, SurgeryAlertRecipient, Facility, SurgeryProcedureTemplate,
)
from app.routers.auth import require_permission


router = APIRouter(prefix="/surgery", tags=["surgery-config"])


# ─── Defaults (used when a config key has no row yet) ───────────────

CONFIG_DEFAULTS = {
    "office_full_threshold":     6,
    "office_lookahead_days":     6,
    "hospital_lookahead_days":  14,
}

ALERT_KINDS = ("office_release", "hospital_release")
PROCEDURE_KINDS = ("minor", "major", "office", "robotic_180", "robotic_240")


# ─── Pydantic shapes ────────────────────────────────────────────────

class ConfigPayload(BaseModel):
    office_full_threshold:     Optional[int] = None
    office_lookahead_days:     Optional[int] = None
    hospital_lookahead_days:   Optional[int] = None


class RecipientIn(BaseModel):
    alert_kind: str
    email: str


class FacilityIn(BaseModel):
    code: str
    label: str
    address: Optional[str] = None
    is_active: bool = True
    sort_order: int = 100


class FacilityPatch(BaseModel):
    code: Optional[str] = None
    label: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class TemplateIn(BaseModel):
    code: str
    name: str
    procedure_kind: str
    default_duration_minutes: int
    default_cpt_code: Optional[str] = None
    is_active: bool = True


class TemplatePatch(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    procedure_kind: Optional[str] = None
    default_duration_minutes: Optional[int] = None
    default_cpt_code: Optional[str] = None
    is_active: Optional[bool] = None


# ─── Config (key/value) ─────────────────────────────────────────────

def _read_config(db: Session) -> dict:
    rows = db.query(SurgeryConfig).all()
    out = dict(CONFIG_DEFAULTS)
    for r in rows:
        out[r.key] = r.value
    return out


@router.get("/config")
def get_config(db: Session = Depends(get_db),
               current_user: dict = Depends(require_permission("claim:read"))):
    return _read_config(db)


@router.put("/config")
def put_config(payload: ConfigPayload,
               db: Session = Depends(get_db),
               current_user: dict = Depends(require_permission("user:manage"))):
    actor = current_user.get("email") or "system"
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k not in CONFIG_DEFAULTS:
            continue
        row = db.query(SurgeryConfig).filter(SurgeryConfig.key == k).first()
        if row is None:
            db.add(SurgeryConfig(key=k, value=v, updated_by=actor))
        else:
            row.value = v
            row.updated_by = actor
    db.commit()
    return _read_config(db)


# ─── Alert recipients ───────────────────────────────────────────────

@router.get("/admin/alert-recipients")
def list_recipients(db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("claim:read"))):
    rows = db.query(SurgeryAlertRecipient).all()
    out = {k: [] for k in ALERT_KINDS}
    for r in rows:
        out.setdefault(r.alert_kind, []).append(r.email)
    for k in out:
        out[k].sort()
    return out


@router.post("/admin/alert-recipients", status_code=201)
def add_recipient(payload: RecipientIn,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(require_permission("user:manage"))):
    if payload.alert_kind not in ALERT_KINDS:
        raise HTTPException(status_code=422,
                            detail=f"unknown alert_kind: {payload.alert_kind}")
    email = payload.email.strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="email required")
    actor = current_user.get("email") or "system"
    exists = (db.query(SurgeryAlertRecipient)
                .filter(SurgeryAlertRecipient.alert_kind == payload.alert_kind,
                         SurgeryAlertRecipient.email == email).first())
    if exists:
        raise HTTPException(status_code=409, detail="recipient already exists")
    row = SurgeryAlertRecipient(alert_kind=payload.alert_kind,
                                  email=email, added_by=actor)
    db.add(row)
    db.commit()
    return {"id": str(row.id), "alert_kind": row.alert_kind, "email": row.email}


@router.delete("/admin/alert-recipients", status_code=204)
def delete_recipient(alert_kind: str, email: str,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(require_permission("user:manage"))):
    row = (db.query(SurgeryAlertRecipient)
             .filter(SurgeryAlertRecipient.alert_kind == alert_kind,
                      SurgeryAlertRecipient.email == email.strip().lower())
             .first())
    if row:
        db.delete(row)
        db.commit()
    return None
