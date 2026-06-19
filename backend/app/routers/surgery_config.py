"""Surgery module config + admin endpoints (Phase B).

Permissions:
  GET picklist endpoints                          claim:read
  All admin endpoints (POST/PUT/PATCH/DELETE)     user:manage
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
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

class CapacityOption(BaseModel):
    case_kind: str
    max: int = Field(ge=1, le=20)

    @field_validator("case_kind")
    @classmethod
    def known_kind(cls, v):
        if v not in PROCEDURE_KINDS:
            raise ValueError(f"unknown case_kind {v}")
        return v


class MinorAddon(BaseModel):
    after_count: int = Field(ge=0, le=20)
    blocked_at: int = Field(ge=1, le=20)


class FacilityCapacity(BaseModel):
    kind: str                                  # robotic | mix_exclusive | fixed_slots
    options: list[CapacityOption] = []
    exclusive: bool = True
    minor_addon: Optional[MinorAddon] = None
    slot_times: Optional[list[str]] = None     # fixed_slots only, "HH:MM"

    @field_validator("kind")
    @classmethod
    def known_capacity_kind(cls, v):
        if v not in ("robotic", "mix_exclusive", "fixed_slots"):
            raise ValueError(f"unknown capacity kind {v}")
        return v

    @model_validator(mode="after")
    def options_required_for_count_kinds(self):
        # robotic / mix_exclusive cap by per-case-kind counts in
        # `options`. An empty options list there means the facility
        # rejects every case (audit #27). fixed_slots caps by slot_times,
        # so it doesn't need options.
        if self.kind in ("robotic", "mix_exclusive") and not self.options:
            raise ValueError(f"{self.kind} capacity requires at least one option")
        return self

    @field_validator("slot_times")
    @classmethod
    def valid_slot_times(cls, v):
        if v is None:
            return v
        if len(set(v)) != len(v):
            raise ValueError("slot times must be distinct")
        for t in v:
            if not re.fullmatch(r"\d{2}:\d{2}", t):
                raise ValueError(f"slot time {t!r} must be HH:MM")
        return sorted(v)


class PostOpVisitIn(BaseModel):
    label: str
    offset_days: int = Field(ge=1, le=365)
    mode: str = "office"                       # office | telehealth
    location_locked: bool = False

    @field_validator("mode")
    @classmethod
    def known_mode(cls, v):
        if v not in ("office", "telehealth"):
            raise ValueError("mode must be office or telehealth")
        return v


class PostOpRuleIn(BaseModel):
    match: list[str] = Field(min_length=1)     # keywords, lowercase
    visits: list[PostOpVisitIn] = Field(min_length=1)


class ConfigPayload(BaseModel):
    # pre-existing
    office_full_threshold:     Optional[int] = Field(default=None, ge=1, le=20)
    office_lookahead_days:     Optional[int] = Field(default=None, ge=1, le=60)
    hospital_lookahead_days:   Optional[int] = Field(default=None, ge=1, le=90)
    reminder_lead_days:        Optional[list[int]] = None
    # alerts & windows
    critical_overdue_hours:    Optional[int] = Field(default=None, ge=1, le=720)
    labs_alert_window_days:    Optional[int] = Field(default=None, ge=1, le=60)
    post_op_docs_alert_days:   Optional[int] = Field(default=None, ge=1, le=60)
    unresponsive_after_days:   Optional[int] = Field(default=None, ge=1, le=365)
    preop_valid_days:          Optional[int] = Field(default=None, ge=30, le=730)
    schedule_horizon_days:     Optional[int] = Field(default=None, ge=30, le=730)
    completed_window_days:     Optional[int] = Field(default=None, ge=1, le=365)
    # cancellation fee (plain scalars, full-replace)
    cancellation_fee_amount:      Optional[int] = Field(default=None, ge=0, le=100000)
    cancellation_fee_days_before: Optional[int] = Field(default=None, ge=0, le=365)
    # steps engine
    step_expected_days_hospital: Optional[dict[str, int]] = None
    step_expected_days_office:   Optional[dict[str, int]] = None
    step_titles_hospital:        Optional[dict[str, str]] = None
    step_titles_office:          Optional[dict[str, str]] = None
    # structured
    post_op_schedules:         Optional[list[PostOpRuleIn]] = None
    capacity_rules:            Optional[dict[str, FacilityCapacity]] = None
    # intake option lists (full-replace string lists)
    clearance_types:           Optional[list[str]] = None
    surgery_device_types:      Optional[list[str]] = None
    assistant_surgeons:        Optional[list[str]] = None
    # payer-ID → insurance-company map (full-replace dict)
    payer_id_insurance_map:    Optional[dict[str, str]] = None
    # boarding-slip email (manual send + scheduled auto-send)
    boarding_slip_recipients_medstar: Optional[list[str]] = None
    boarding_slip_recipients_crmc:    Optional[list[str]] = None
    boarding_slip_auto_email_enabled: Optional[bool] = None
    boarding_slip_auto_email_hours:   Optional[int] = Field(default=None, ge=0, le=336)

    @field_validator("boarding_slip_recipients_medstar",
                     "boarding_slip_recipients_crmc")
    @classmethod
    def boarding_slip_recipients_valid(cls, v):
        # Per-facility recipient email lists. Strip + lowercase each entry,
        # drop blanks, dedupe (order preserved). Reject any remaining entry
        # without an "@" so a typo can't silently store an unmailable
        # address. An empty list is allowed (means "no recipients").
        if v is None:
            return v
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            e = (item or "").strip().lower()
            if not e:
                continue
            if "@" not in e:
                raise ValueError(f"invalid email: {e}")
            if e not in seen:
                seen.add(e)
                out.append(e)
        return out

    @field_validator("step_expected_days_hospital", "step_expected_days_office")
    @classmethod
    def days_in_range(cls, v):
        if v is None:
            return v
        for k, d in v.items():
            if not (1 <= int(d) <= 90):
                raise ValueError(f"expected days for {k} must be 1-90")
        return v

    @field_validator("reminder_lead_days")
    @classmethod
    def reminder_lead_days_valid(cls, v):
        # Non-empty, each 1..60. An empty list would silently disable all
        # reminders with no explicit signal (audit #8).
        if v is None:
            return v
        if not v:
            raise ValueError("reminder_lead_days must not be empty")
        for d in v:
            if not (1 <= int(d) <= 60):
                raise ValueError(f"reminder lead day {d} must be 1-60")
        return v

    @field_validator("clearance_types", "surgery_device_types", "assistant_surgeons")
    @classmethod
    def option_list_valid(cls, v):
        # Simple string lists: non-empty list, each entry non-blank once
        # stripped, deduped, order preserved. An empty list or a blank
        # entry is rejected so a save can't silently wipe the options.
        if v is None:
            return v
        if not v:
            raise ValueError("option list must not be empty")
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            s = (item or "").strip()
            if not s:
                raise ValueError("option list entries must not be blank")
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    @field_validator("payer_id_insurance_map")
    @classmethod
    def payer_id_map_valid(cls, v):
        # Maps a payer ID (3-6 ALPHANUMERIC chars) to a non-empty company
        # name. Keys are uppercase-normalized for storage so lookups can be
        # case-insensitive. Numeric IDs (e.g. "75191") still pass since
        # digits are alphanumeric. Full-replaces the stored map on PUT (not
        # in the deep/facility merge sets), so reject bad shapes rather than
        # silently storing garbage.
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError("payer_id_insurance_map must be an object")
        out: dict[str, str] = {}
        for key, val in v.items():
            k = (str(key) if key is not None else "").strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{3,6}", k):
                raise ValueError(
                    f"payer ID {key!r} must be 3-6 alphanumeric chars")
            company = (val or "").strip() if isinstance(val, str) else ""
            if not company:
                raise ValueError(f"company for payer ID {k} must not be blank")
            out[k] = company
        return out


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


# Dict-valued config keys that must MERGE incoming sub-keys into the
# stored dict instead of wholesale-replacing it. StepsTab/capacity UIs
# send only the touched sub-keys, so a full replace would silently wipe
# previously-saved entries (audit #7). All four step_* keys deep-merge at
# the sub-key level; capacity_rules merges at the facility level (an
# incoming facility's rule replaces the stored one, but facilities the
# payload omits are preserved). Everything else (post_op_schedules — a
# LIST — and scalar keys) is full-replace.
_DEEP_MERGE_KEYS = (
    "step_expected_days_hospital",
    "step_expected_days_office",
    "step_titles_hospital",
    "step_titles_office",
)
_FACILITY_MERGE_KEYS = ("capacity_rules",)


@router.put("/config")
def put_config(payload: ConfigPayload,
               db: Session = Depends(get_db),
               current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    actor = current_user.get("email") or "system"
    data = payload.model_dump(exclude_unset=True, mode="json")
    for k, v in data.items():
        if k not in CONFIG_DEFAULTS:
            continue
        row = db.query(SurgeryConfig).filter(SurgeryConfig.key == k).first()
        # For dict-valued keys, merge the incoming dict into the stored
        # one rather than replacing it. Both _DEEP_MERGE_KEYS and
        # _FACILITY_MERGE_KEYS merge one level deep (incoming sub-keys /
        # facilities override; omitted ones are preserved). The values
        # are flat enough (int/str for steps, a full facility rule for
        # capacity) that a single-level merge is the correct semantics.
        if (k in _DEEP_MERGE_KEYS or k in _FACILITY_MERGE_KEYS) and isinstance(v, dict):
            existing = dict(row.value) if (row is not None and isinstance(row.value, dict)) else {}
            existing.update(v)
            v = existing
        if row is None:
            db.add(SurgeryConfig(key=k, value=v, updated_by=actor))
        else:
            row.value = v
            row.updated_by = actor
    db.commit()
    return _read_config(db)


@router.get("/config/step-catalog")
def step_catalog(current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    from app.services.surgery.step_engine import (
        HOSPITAL_STEPS, OFFICE_STEPS,
        DEFAULT_EXPECTED_DAYS_HOSPITAL, DEFAULT_EXPECTED_DAYS_OFFICE,
    )

    def ser(steps, days):
        return [{"n": st.n, "key": st.key, "title": st.title,
                 "optional": st.optional, "default_days": days[st.key]}
                for st in steps]

    return {"hospital": ser(HOSPITAL_STEPS, DEFAULT_EXPECTED_DAYS_HOSPITAL),
            "office":   ser(OFFICE_STEPS,   DEFAULT_EXPECTED_DAYS_OFFICE)}


@router.get("/config/post-op-defaults")
def post_op_defaults(current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    from app.services.post_op_schedule import DEFAULT_PROCEDURE_RULES
    return {"rules": [
        {"match": kws, "visits": [
            {"label": v.label, "offset_days": v.days_post_op,
             "mode": v.suggested_location, "location_locked": v.location_locked}
            for v in visits]}
        for kws, visits in DEFAULT_PROCEDURE_RULES]}


@router.get("/config/capacity-defaults")
def capacity_defaults(current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.VIEW))):
    from app.services.surgery.block_schedule import DEFAULT_CAPACITY_RULES, DURATIONS
    return {"defaults": DEFAULT_CAPACITY_RULES, "durations": DURATIONS}


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
