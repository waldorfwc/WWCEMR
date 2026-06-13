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
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier


router = APIRouter(prefix="/surgery", tags=["surgery-config"])


# ─── Defaults (used when a config key has no row yet) ───────────────
# Source of truth lives in the registry; import it here so _read_config
# and put_config keep working unchanged.
from app.services.surgery.settings import SETTINGS_DEFAULTS as CONFIG_DEFAULTS  # noqa: E402

ALERT_KINDS = ("office_release", "hospital_release")
PROCEDURE_KINDS = ("minor", "major", "office", "robotic_180", "robotic_240")


# ─── Pydantic shapes ────────────────────────────────────────────────

class ConfigPayload(BaseModel):
    office_full_threshold:     Optional[int]       = None
    office_lookahead_days:     Optional[int]       = None
    hospital_lookahead_days:   Optional[int]       = None
    reminder_lead_days:        Optional[list[int]] = None


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
               current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    return _read_config(db)


@router.put("/config")
def put_config(payload: ConfigPayload,
               db: Session = Depends(get_db),
               current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
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
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
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
                  current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
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
                     current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    row = (db.query(SurgeryAlertRecipient)
             .filter(SurgeryAlertRecipient.alert_kind == alert_kind,
                      SurgeryAlertRecipient.email == email.strip().lower())
             .first())
    if row:
        db.delete(row)
        db.commit()
    return None


# ─── Facilities ─────────────────────────────────────────────────────

def _facility_dict(f: Facility) -> dict:
    return {"id": str(f.id), "code": f.code, "label": f.label,
            "address": f.address, "is_active": f.is_active,
            "sort_order": f.sort_order}


@router.get("/admin/facilities")
def list_facilities_admin(db: Session = Depends(get_db),
                           current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    rows = (db.query(Facility)
              .order_by(Facility.sort_order.asc(), Facility.label.asc()).all())
    return {"facilities": [_facility_dict(f) for f in rows]}


@router.get("/picklists/facilities")
def list_facilities_picklist(db: Session = Depends(get_db),
                              current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    rows = (db.query(Facility)
              .filter(Facility.is_active.is_(True))
              .order_by(Facility.sort_order.asc(), Facility.label.asc()).all())
    return {"facilities": [_facility_dict(f) for f in rows]}


@router.post("/admin/facilities", status_code=201)
def create_facility(payload: FacilityIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    code = (payload.code or "").strip().lower()
    label = (payload.label or "").strip()
    if not code or not label:
        raise HTTPException(status_code=422, detail="code and label required")
    if db.query(Facility).filter(Facility.code == code).first():
        raise HTTPException(status_code=409, detail="code already exists")
    actor = current_user.get("email") or "system"
    f = Facility(code=code, label=label, address=payload.address,
                  is_active=payload.is_active, sort_order=payload.sort_order,
                  created_by=actor, updated_by=actor)
    db.add(f); db.commit(); db.refresh(f)
    return _facility_dict(f)


@router.patch("/admin/facilities/{facility_id}")
def patch_facility(facility_id: str, payload: FacilityPatch,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    f = db.query(Facility).filter(Facility.id == facility_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="facility not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(f, k, v)
    f.updated_by = current_user.get("email") or "system"
    db.commit(); db.refresh(f)
    return _facility_dict(f)


@router.delete("/admin/facilities/{facility_id}", status_code=204)
def delete_facility(facility_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    f = db.query(Facility).filter(Facility.id == facility_id).first()
    if f:
        db.delete(f); db.commit()
    return None


# ─── Procedure templates ────────────────────────────────────────────

def _template_dict(t: SurgeryProcedureTemplate) -> dict:
    return {"id": str(t.id), "code": t.code, "name": t.name,
            "procedure_kind": t.procedure_kind,
            "default_duration_minutes": t.default_duration_minutes,
            "default_cpt_code": t.default_cpt_code,
            "is_active": t.is_active}


@router.get("/admin/procedure-templates")
def list_templates_admin(db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    rows = db.query(SurgeryProcedureTemplate).order_by(
        SurgeryProcedureTemplate.name.asc()).all()
    return {"templates": [_template_dict(t) for t in rows]}


@router.get("/picklists/procedure-templates")
def list_templates_picklist(db: Session = Depends(get_db),
                             current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    rows = (db.query(SurgeryProcedureTemplate)
              .filter(SurgeryProcedureTemplate.is_active.is_(True))
              .order_by(SurgeryProcedureTemplate.name.asc()).all())
    return {"templates": [_template_dict(t) for t in rows]}


@router.post("/admin/procedure-templates", status_code=201)
def create_template(payload: TemplateIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    if payload.procedure_kind not in PROCEDURE_KINDS:
        raise HTTPException(status_code=422,
                            detail=f"unknown procedure_kind: {payload.procedure_kind}")
    if payload.default_duration_minutes <= 0:
        raise HTTPException(status_code=422, detail="duration must be > 0")
    actor = current_user.get("email") or "system"
    if db.query(SurgeryProcedureTemplate).filter(
            SurgeryProcedureTemplate.code == payload.code).first():
        raise HTTPException(status_code=409, detail="code already exists")
    t = SurgeryProcedureTemplate(
        code=payload.code, name=payload.name,
        procedure_kind=payload.procedure_kind,
        default_duration_minutes=payload.default_duration_minutes,
        default_cpt_code=payload.default_cpt_code,
        is_active=payload.is_active, created_by=actor, updated_by=actor,
    )
    db.add(t); db.commit(); db.refresh(t)
    return _template_dict(t)


@router.patch("/admin/procedure-templates/{template_id}")
def patch_template(template_id: str, payload: TemplatePatch,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    t = db.query(SurgeryProcedureTemplate).filter(
            SurgeryProcedureTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    data = payload.model_dump(exclude_unset=True)
    if "procedure_kind" in data and data["procedure_kind"] not in PROCEDURE_KINDS:
        raise HTTPException(status_code=422,
                            detail=f"unknown procedure_kind: {data['procedure_kind']}")
    for k, v in data.items():
        setattr(t, k, v)
    t.updated_by = current_user.get("email") or "system"
    db.commit(); db.refresh(t)
    return _template_dict(t)


@router.delete("/admin/procedure-templates/{template_id}", status_code=204)
def delete_template(template_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    t = db.query(SurgeryProcedureTemplate).filter(
            SurgeryProcedureTemplate.id == template_id).first()
    if t:
        db.delete(t); db.commit()
    return None


# ─── Email templates (Phase I) ─────────────────────────────────────

from app.models.patient_email import EmailTemplate, EMAIL_TEMPLATE_KINDS


class EmailTemplatePatch(BaseModel):
    label:     Optional[str] = None
    subject:   Optional[str] = None
    html_body: Optional[str] = None
    text_body: Optional[str] = None
    is_active: Optional[bool] = None
    notes:     Optional[str] = None


class EmailTemplatePreviewIn(BaseModel):
    subject:   str
    html_body: str
    context:   dict


def _email_template_dict(t: EmailTemplate) -> dict:
    return {
        "id":         str(t.id),
        "kind":       t.kind,
        "label":      t.label,
        "subject":    t.subject,
        "html_body":  t.html_body,
        "text_body":  t.text_body,
        "is_active":  t.is_active,
        "notes":      t.notes,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "updated_by": t.updated_by,
    }


@router.get("/admin/email-templates")
def list_email_templates(db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    rows = db.query(EmailTemplate).order_by(EmailTemplate.label.asc()).all()
    return {
        "templates": [_email_template_dict(t) for t in rows],
        "allowed_kinds": list(EMAIL_TEMPLATE_KINDS),
    }


@router.patch("/admin/email-templates/{template_id}")
def patch_email_template(template_id: str,
                          payload: EmailTemplatePatch,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    t = db.query(EmailTemplate).filter(EmailTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(t, k, v)
    t.updated_by = current_user.get("email") or "system"
    db.commit(); db.refresh(t)
    return _email_template_dict(t)


@router.post("/admin/email-templates/preview")
def preview_email_template(payload: EmailTemplatePreviewIn,
                            current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    """Render subject + html with provided context. No DB writes, no send."""
    from app.services.patient_email import render
    return {
        "subject":   render(payload.subject, payload.context or {}),
        "html_body": render(payload.html_body, payload.context or {}),
    }


# ─── SMS templates (Phase J) ───────────────────────────────────────

from app.models.patient_sms import SmsTemplate, SMS_TEMPLATE_KINDS


class SmsTemplatePatch(BaseModel):
    label:     Optional[str] = None
    body:      Optional[str] = None
    is_active: Optional[bool] = None
    notes:     Optional[str] = None


class SmsTemplatePreviewIn(BaseModel):
    body:    str
    context: dict


def _sms_template_dict(t: SmsTemplate) -> dict:
    return {
        "id":         str(t.id),
        "kind":       t.kind,
        "label":      t.label,
        "body":       t.body,
        "is_active":  t.is_active,
        "notes":      t.notes,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "updated_by": t.updated_by,
    }


@router.get("/admin/sms-templates")
def list_sms_templates(db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    rows = db.query(SmsTemplate).order_by(SmsTemplate.label.asc()).all()
    return {
        "templates":     [_sms_template_dict(t) for t in rows],
        "allowed_kinds": list(SMS_TEMPLATE_KINDS),
    }


@router.patch("/admin/sms-templates/{template_id}")
def patch_sms_template(template_id: str,
                        payload: SmsTemplatePatch,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    t = db.query(SmsTemplate).filter(SmsTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(t, k, v)
    t.updated_by = current_user.get("email") or "system"
    db.commit(); db.refresh(t)
    return _sms_template_dict(t)


@router.post("/admin/sms-templates/preview")
def preview_sms_template(payload: SmsTemplatePreviewIn,
                          current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    """Render body with context. Returns body + segment count."""
    from app.services.patient_sms import render, _segments
    body = render(payload.body, payload.context or {})
    return {"body": body, "length": len(body), "segments": _segments(body)}
