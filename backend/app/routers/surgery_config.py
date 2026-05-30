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
