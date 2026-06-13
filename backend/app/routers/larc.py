"""LARC (Long-Acting Reversible Contraceptive) device inventory + tracking.

Phase 1 endpoints:
  GET  /larc/dashboard         — counts per bucket, on-hand by type, alerts
  GET  /larc/devices           — paginated device list with filters
  POST /larc/devices           — add a new physical device to inventory
  GET  /larc/devices/{id}      — single device detail + assignment + audit
  PATCH /larc/devices/{id}     — edit device fields
  GET  /larc/device-types      — picklist of registered types
  GET  /larc/pharmacies        — pharmacy directory
  POST /larc/pharmacies        — add a pharmacy
  GET  /larc/assignments       — list / filter assignments (the workflow rows)
  POST /larc/assignments       — create an assignment from a ModMed request
  GET  /larc/audit             — filterable audit log
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime, timedelta
from app.utils.dt import now_utc_naive
from typing import Optional

from typing import Annotated
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field


# Sanity bounds on user-entered money fields. The practice's
# "money sanity ceiling" rule (project memory feedback_money_sanity_ceiling)
# treats anything above $50K as a column-shift artifact; staff typing in a
# benefits worksheet shouldn't ever cross that line on a single line-item
# field. Reject the input (Pydantic 422) instead of silently storing
# unrealistic numbers that later contaminate reports.
DollarAmount = Annotated[float, Field(ge=0, le=50_000, allow_inf_nan=False)]
PercentAmount = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models.larc import (
    LarcAssignment, LarcAuditEvent, LarcCheckout, LarcDevice,
    LarcDeviceType, LarcEnrollmentEnvelope, LarcInventoryCount,
    LarcManualSection, LarcMilestone, LarcOwedPatient, LarcPharmacy,
)
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_super_admin, requires_tier
from app.services.audit_service import log_action
from app.services.larc.workflow import (
    ALL_BUCKETS, ASSIGNMENT_REALLOCATE_AFTER_DAYS, CHECKOUT_ACK_WINDOW_HOURS,
    DEVICE_EXPIRY_HOLD_DAYS, LOCATIONS, LOCATION_LABELS,
    PHARMACY_ORDER_SLA_DAYS, assignment_buckets, log_audit, spawn_milestones,
)

router = APIRouter(prefix="/larc", tags=["larc"])


# ─── Serializers ────────────────────────────────────────────────────

def _device_dict(d: LarcDevice) -> dict:
    return {
        "id": str(d.id),
        "our_id": d.our_id,
        "manufacturer_lot": d.manufacturer_lot,
        "manufacturer_serial": d.manufacturer_serial,
        "device_type_id": str(d.device_type_id),
        "device_type_name": d.device_type.name if d.device_type else None,
        "category": (d.device_type.category if d.device_type else None) or "larc",
        "purchase_date": str(d.purchase_date) if d.purchase_date else None,
        "purchase_price": (str(d.purchase_price) if d.purchase_price is not None else None),
        "expiration_date": str(d.expiration_date) if d.expiration_date else None,
        "location": d.location,
        "location_label": LOCATION_LABELS.get(d.location, d.location),
        "status": d.status,
        "ownership": d.ownership or "wwc_owned",
        "ownership_label": {
            "patient_owned": "Patient Owned",
            "wwc_owned":     "WWC Owned",
            "wwc_claimed":   "WWC Claimed",
        }.get(d.ownership or "wwc_owned"),
        "purchasing_patient_chart": d.purchasing_patient_chart,
        "purchasing_patient_name":  d.purchasing_patient_name,
        "replacement_device_id": str(d.replacement_device_id) if d.replacement_device_id else None,
        "replaces_device_id": str(d.replaces_device_id) if d.replaces_device_id else None,
        "notes": d.notes,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _resolve_device_type_name(a: LarcAssignment) -> Optional[str]:
    """Prefer the attached device's type; fall back to the assignment's
    pinned device_type_id (set for pharmacy-order rows before the device
    ships). Single SELECT either way."""
    if a.device and a.device.device_type:
        return a.device.device_type.name
    if a.device_type_id:
        from sqlalchemy.orm import object_session
        sess = object_session(a)
        if sess is None:
            return None
        dt = (sess.query(LarcDeviceType)
                  .filter(LarcDeviceType.id == a.device_type_id)
                  .first())
        return dt.name if dt else None
    return None


def _latest_envelope_dict(a: LarcAssignment) -> Optional[dict]:
    """Compact summary of the most recent LarcEnrollmentEnvelope for this
    assignment. Returns None if no envelope has been sent. Drives the
    Pharmacy Enrollment status panel on the LarcAssignment page."""
    from sqlalchemy.orm import object_session
    sess = object_session(a)
    if sess is None:
        return None
    env = (sess.query(LarcEnrollmentEnvelope)
              .filter(LarcEnrollmentEnvelope.assignment_id == a.id)
              .order_by(LarcEnrollmentEnvelope.created_at.desc())
              .first())
    if env is None:
        return None
    return {
        "id": str(env.id),
        "boldsign_envelope_id": env.boldsign_envelope_id,
        "boldsign_template_id": env.boldsign_template_id,
        "status": env.status,
        "sent_at":                 env.sent_at.isoformat() if env.sent_at else None,
        "receptionist_signed_at":  env.receptionist_signed_at.isoformat() if env.receptionist_signed_at else None,
        "patient_signed_at":       env.patient_signed_at.isoformat() if env.patient_signed_at else None,
        "provider_signed_at":      env.provider_signed_at.isoformat() if env.provider_signed_at else None,
        "signed_at":               env.signed_at.isoformat() if env.signed_at else None,
        "declined_at":             env.declined_at.isoformat() if env.declined_at else None,
        "voided_at":               env.voided_at.isoformat() if env.voided_at else None,
        "faxed_at":                env.faxed_at.isoformat() if env.faxed_at else None,
        "fax_status":              env.fax_status,
        "fax_to":                  env.fax_to,
        "fax_attempts":            env.fax_attempts,
        "last_fax_error":          env.last_fax_error,
        "sent_by":                 env.sent_by,
    }


def _assignment_dict(a: LarcAssignment, include_milestones: bool = False) -> dict:
    out = {
        "id": str(a.id),
        "device_id": str(a.device_id) if a.device_id else None,
        "device_type_id": str(a.device_type_id) if a.device_type_id else None,
        "device_our_id": a.device.our_id if a.device else None,
        "device_type_name": _resolve_device_type_name(a),
        "device_ownership": (a.device.ownership if a.device else None),
        "device_received_date":
            (str(a.device.purchase_date) if a.device and a.device.purchase_date else None),
        "device_purchase_price":
            (str(a.device.purchase_price) if a.device and a.device.purchase_price is not None else None),
        "device_typical_cost":
            (str(a.device.device_type.typical_cost)
              if a.device and a.device.device_type and a.device.device_type.typical_cost is not None
              else None),
        "chart_number": a.chart_number,
        "patient_name": a.patient_name,
        "patient_first_name":     a.patient_first_name,
        "patient_middle_initial": a.patient_middle_initial,
        "patient_last_name":      a.patient_last_name,
        "patient_dob": str(a.patient_dob) if a.patient_dob else None,
        "patient_email": a.patient_email,
        "patient_phone": a.patient_phone,
        "patient_cell":  a.patient_cell,
        "patient_address": a.patient_address,
        "patient_city":    a.patient_city,
        "patient_state":   a.patient_state,
        "patient_zip":     a.patient_zip,
        "primary_insurance":   a.primary_insurance,
        "insurance_policy_no": a.insurance_policy_no,
        "insurance_group_no":  a.insurance_group_no,
        "has_insurance_card":  bool(a.insurance_card_key),
        "insurance_card_filename": a.insurance_card_filename,
        "pharmacy_id": str(a.pharmacy_id) if a.pharmacy_id else None,
        "source_flow": a.source_flow,
        "linked_surgery_id": str(a.linked_surgery_id) if a.linked_surgery_id else None,
        "status": a.status,
        "sub_flag": a.sub_flag,
        "is_active": bool(a.is_active),
        "patient_responsibility": (str(a.patient_responsibility)
                                    if a.patient_responsibility is not None else None),
        "allowed_amount":   (str(a.allowed_amount)   if a.allowed_amount   is not None else None),
        "deductible":       (str(a.deductible)       if a.deductible       is not None else None),
        "deductible_met":   (str(a.deductible_met)   if a.deductible_met   is not None else None),
        "copay":            (str(a.copay)            if a.copay            is not None else None),
        "coinsurance_pct":  (str(a.coinsurance_pct)  if a.coinsurance_pct  is not None else None),
        "oop_max":          (str(a.oop_max)          if a.oop_max          is not None else None),
        "oop_met":          (str(a.oop_met)          if a.oop_met          is not None else None),
        "benefits_verified_at": (str(a.benefits_verified_at) if a.benefits_verified_at else None),
        "patient_paid_at": a.patient_paid_at.isoformat() if a.patient_paid_at else None,
        "patient_paid_by": a.patient_paid_by,
        "patient_paid_amount": (str(a.patient_paid_amount)
                                if a.patient_paid_amount is not None else None),
        "claim_number": a.claim_number,
        "billed_at": a.billed_at.isoformat() if a.billed_at else None,
        "billed_by": a.billed_by,
        "enrollment_sent_at": a.enrollment_sent_at.isoformat() if a.enrollment_sent_at else None,
        "enrollment_signed_at": a.enrollment_signed_at.isoformat() if a.enrollment_signed_at else None,
        "inserting_provider_email": a.inserting_provider_email,
        "inserting_provider_name":  a.inserting_provider_name,
        "inserting_provider_npi":   a.inserting_provider_npi,
        "app_email": a.app_email,
        "app_name":  a.app_name,
        "app_npi":   a.app_npi,
        "latest_envelope": _latest_envelope_dict(a),
        "request_faxed_at": a.request_faxed_at.isoformat() if a.request_faxed_at else None,
        "expected_received_by": str(a.expected_received_by) if a.expected_received_by else None,
        "device_received_at": a.device_received_at.isoformat() if a.device_received_at else None,
        "patient_notified_at": a.patient_notified_at.isoformat() if a.patient_notified_at else None,
        "appt_scheduled_at": a.appt_scheduled_at.isoformat() if a.appt_scheduled_at else None,
        "appt_date": str(a.appt_date) if a.appt_date else None,
        "inserted_at": a.inserted_at.isoformat() if a.inserted_at else None,
        "failure_reason": a.failure_reason,
        "buckets": sorted(assignment_buckets(a)),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
    if include_milestones:
        out["milestones"] = [
            {
                "id": str(m.id), "kind": m.kind, "title": m.title,
                "position": m.position, "status": m.status,
                "completed_at": m.completed_at.isoformat() if m.completed_at else None,
                "completed_by": m.completed_by,
                "notes": m.notes,
                "expected_duration_days": m.expected_duration_days,
            }
            for m in (a.milestones or [])
        ]
    return out


# ─── Dashboard ──────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db),
               current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    today = _date.today()

    # On-hand device counts by type + location + category
    devices = (db.query(LarcDevice)
                 .options(joinedload(LarcDevice.device_type))
                 .filter(LarcDevice.status.in_(["unassigned", "assigned", "received"]))
                 .all())
    on_hand_by_type: dict = {}
    on_hand_by_location: dict = {loc: 0 for loc in LOCATIONS}
    on_hand_by_category: dict = {"larc": 0, "office_procedure": 0}
    device_categories: dict = {}   # device_type name → category
    for d in devices:
        t = d.device_type.name if d.device_type else "Unknown"
        cat = (d.device_type.category if d.device_type else None) or "larc"
        on_hand_by_type[t] = on_hand_by_type.get(t, 0) + 1
        on_hand_by_location[d.location] = on_hand_by_location.get(d.location, 0) + 1
        on_hand_by_category[cat] = on_hand_by_category.get(cat, 0) + 1
        device_categories[t] = cat

    # Reorder alerts — in-stock device types at or below threshold
    reorder_alerts = []
    for dt in db.query(LarcDeviceType).filter(LarcDeviceType.reorder_threshold.isnot(None)).all():
        on_hand = on_hand_by_type.get(dt.name, 0)
        if on_hand <= (dt.reorder_threshold or 0):
            reorder_alerts.append({
                "device_type": dt.name,
                "category": dt.category or "larc",
                "on_hand": on_hand,
                "threshold": dt.reorder_threshold,
                "suggested_quantity": dt.reorder_quantity,
            })

    # Expiring soon — within DEVICE_EXPIRY_HOLD_DAYS (365 days)
    horizon = today + timedelta(days=DEVICE_EXPIRY_HOLD_DAYS)
    expiring = (db.query(LarcDevice)
                  .options(joinedload(LarcDevice.device_type))
                  .filter(LarcDevice.expiration_date.isnot(None),
                          LarcDevice.expiration_date <= horizon,
                          LarcDevice.status.in_(["unassigned", "assigned", "received"]))
                  .order_by(LarcDevice.expiration_date)
                  .limit(20).all())
    expiring_rows = [
        {
            "device_id": str(d.id),
            "our_id": d.our_id,
            "device_type_name": d.device_type.name if d.device_type else None,
            "expiration_date": str(d.expiration_date),
            "days_to_expiry": (d.expiration_date - today).days,
            "status": d.status,
        }
        for d in expiring
    ]

    # Bucket counts — walk active assignments
    active_assignments = (db.query(LarcAssignment)
                            .options(joinedload(LarcAssignment.milestones),
                                     joinedload(LarcAssignment.device))
                            .filter(LarcAssignment.not_deleted(),
                                    LarcAssignment.status.notin_(["billed", "cancelled"]))
                            .all())
    bucket_counts = {b: 0 for b in ALL_BUCKETS}
    for a in active_assignments:
        for b in assignment_buckets(a, today):
            bucket_counts[b] = bucket_counts.get(b, 0) + 1

    # Pharmacy-order overdue (faxed >SLA days, not received)
    overdue_pharmacy = (db.query(LarcAssignment)
                          .options(joinedload(LarcAssignment.device))
                          .filter(LarcAssignment.not_deleted(),
                                  LarcAssignment.source_flow == "pharmacy_order",
                                  LarcAssignment.request_faxed_at.isnot(None),
                                  LarcAssignment.device_received_at.is_(None),
                                  LarcAssignment.request_faxed_at
                                      <= now_utc_naive() - timedelta(days=PHARMACY_ORDER_SLA_DAYS))
                          .order_by(LarcAssignment.request_faxed_at)
                          .limit(20).all())

    # Checkout outstanding ack
    cutoff_ack = now_utc_naive() - timedelta(hours=CHECKOUT_ACK_WINDOW_HOURS)
    unack_checkouts = (db.query(LarcCheckout)
                         .options(joinedload(LarcCheckout.assignment))
                         .filter(LarcCheckout.approval_status == "approved",
                                 LarcCheckout.acknowledged_at.is_(None),
                                 LarcCheckout.requested_at <= cutoff_ack)
                         .order_by(LarcCheckout.requested_at)
                         .limit(20).all())

    # Owed list
    owed = (db.query(LarcOwedPatient)
              .filter(LarcOwedPatient.resolved_at.is_(None))
              .order_by(LarcOwedPatient.owed_since.desc()).limit(20).all())

    return {
        "today": str(today),
        "on_hand_by_type": on_hand_by_type,
        "on_hand_by_location": on_hand_by_location,
        "on_hand_by_category": on_hand_by_category,
        "device_categories": device_categories,
        "reorder_alerts": reorder_alerts,
        "expiring_soon": expiring_rows,
        "buckets": bucket_counts,
        "overdue_pharmacy_orders": [
            {
                "assignment_id": str(a.id),
                "patient_name": a.patient_name,
                "chart_number": a.chart_number,
                "device_type_name": a.device.device_type.name if a.device and a.device.device_type else None,
                "faxed_on": a.request_faxed_at.isoformat() if a.request_faxed_at else None,
                "days_overdue": (now_utc_naive() - a.request_faxed_at).days - PHARMACY_ORDER_SLA_DAYS
                                if a.request_faxed_at else None,
            }
            for a in overdue_pharmacy
        ],
        "unacknowledged_checkouts": [
            {
                "checkout_id": str(c.id),
                "patient_name": c.assignment.patient_name if c.assignment else None,
                "requested_by": c.requested_by,
                "requested_at": c.requested_at.isoformat(),
                "hours_outstanding": int((now_utc_naive() - c.requested_at).total_seconds() // 3600),
            }
            for c in unack_checkouts
        ],
        "owed_patients": [
            {
                "id": str(o.id),
                "chart_number": o.chart_number,
                "patient_name": o.patient_name,
                "owed_since": o.owed_since.isoformat(),
                "expires_at": str(o.expires_at) if o.expires_at else None,
            }
            for o in owed
        ],
    }


# ─── Picklists ──────────────────────────────────────────────────────

@router.get("/picklists")
def get_picklists(current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    from app.services.surgery.picklists import INSURANCE_COMPANIES
    return {
        "locations": [{"v": k, "l": v} for k, v in LOCATION_LABELS.items()],
        "buckets": ALL_BUCKETS,
        "insurance_companies": INSURANCE_COMPANIES,
    }


def _device_type_dict(t: LarcDeviceType) -> dict:
    return {
        "id": str(t.id),
        "name": t.name,
        "manufacturer": t.manufacturer,
        "category": t.category or "larc",
        "default_flow": t.default_flow,
        "typical_cost": str(t.typical_cost) if t.typical_cost is not None else None,
        "reorder_threshold": t.reorder_threshold,
        "reorder_quantity": t.reorder_quantity,
        "enrollment_form_template": t.enrollment_form_template,
        "notes": t.notes,
        "is_active": bool(t.is_active),
    }


@router.get("/device-types")
def list_device_types(db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    rows = db.query(LarcDeviceType).order_by(LarcDeviceType.name).all()
    return [_device_type_dict(t) for t in rows]


class DeviceTypeIn(BaseModel):
    name: str
    manufacturer: Optional[str] = None
    category: str = "larc"   # larc | office_procedure
    default_flow: str = "pharmacy_order"   # in_stock | pharmacy_order | office_procedure
    typical_cost: Optional[DollarAmount] = None
    reorder_threshold: Optional[int] = None
    reorder_quantity: Optional[int] = None
    enrollment_form_template: Optional[str] = None   # DocuSign template_id
    notes: Optional[str] = None
    is_active: bool = True


@router.post("/device-types", status_code=201)
def create_device_type(payload: DeviceTypeIn,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    if payload.category not in ("larc", "office_procedure"):
        raise HTTPException(status_code=422, detail="category must be larc or office_procedure")
    if payload.default_flow not in ("in_stock", "pharmacy_order", "office_procedure"):
        raise HTTPException(status_code=422, detail="default_flow must be in_stock, pharmacy_order, or office_procedure")
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="name is required")
    existing = db.query(LarcDeviceType).filter(LarcDeviceType.name == payload.name.strip()).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Device type '{payload.name}' already exists")
    t = LarcDeviceType(
        name=payload.name.strip(),
        manufacturer=payload.manufacturer,
        category=payload.category,
        default_flow=payload.default_flow,
        typical_cost=payload.typical_cost,
        reorder_threshold=payload.reorder_threshold,
        reorder_quantity=payload.reorder_quantity,
        enrollment_form_template=payload.enrollment_form_template,
        notes=payload.notes,
        is_active=payload.is_active,
    )
    db.add(t); db.flush()
    log_audit(db, actor=current_user.get("email") or "system",
              action="device_type_added",
              summary=f"Added device type {t.name}",
              detail=_device_type_dict(t))
    db.commit(); db.refresh(t)
    return _device_type_dict(t)


class DeviceTypePatch(BaseModel):
    name: Optional[str] = None
    manufacturer: Optional[str] = None
    category: Optional[str] = None
    default_flow: Optional[str] = None
    typical_cost: Optional[DollarAmount] = None
    reorder_threshold: Optional[int] = None
    reorder_quantity: Optional[int] = None
    enrollment_form_template: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


@router.patch("/device-types/{type_id}")
def patch_device_type(type_id: str, payload: DeviceTypePatch,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    t = db.query(LarcDeviceType).filter(LarcDeviceType.id == type_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="device type not found")
    data = payload.model_dump(exclude_unset=True)
    if "category" in data and data["category"] not in ("larc", "office_procedure"):
        raise HTTPException(status_code=422, detail="category must be larc or office_procedure")
    if "default_flow" in data and data["default_flow"] not in ("in_stock", "pharmacy_order", "office_procedure"):
        raise HTTPException(status_code=422, detail="default_flow must be in_stock, pharmacy_order, or office_procedure")
    before = {k: getattr(t, k) for k in data}
    for k, v in data.items():
        setattr(t, k, v)
    log_audit(db, actor=current_user.get("email") or "system",
              action="device_type_edited",
              summary=f"Edited device type {t.name}: {list(data.keys())}",
              detail={"before": {k: (str(v) if v is not None else None) for k, v in before.items()},
                      "after": {k: (str(getattr(t, k)) if getattr(t, k) is not None else None) for k in data}})
    db.commit(); db.refresh(t)
    return _device_type_dict(t)


@router.get("/boldsign-templates")
def list_boldsign_templates(current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    """Pull the live BoldSign template list so admins pick from a dropdown
    instead of hand-typing template IDs."""
    import os
    import httpx
    api_key = os.environ.get("BOLDSIGN_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="BoldSign not configured")
    try:
        r = httpx.get(
            "https://api.boldsign.com/v1/template/list",
            headers={"X-API-KEY": api_key},
            timeout=30,
            params={"PageSize": 50, "Page": 1},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"BoldSign unreachable: {exc}")
    if r.status_code != 200:
        raise HTTPException(status_code=502,
                             detail=f"BoldSign returned {r.status_code}: {r.text[:200]}")
    data = r.json()
    return {
        "templates": [
            {
                "id": t.get("documentId"),
                "name": t.get("messageTitle") or t.get("templateName"),
            }
            for t in data.get("result", [])
        ]
    }


# ─── Pharmacies ─────────────────────────────────────────────────────

class PharmacyIn(BaseModel):
    name: str
    fax: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    accepts_insurance: Optional[list[str]] = None
    device_names: Optional[list[str]] = None
    default_for_devices: Optional[list[str]] = None
    notes: Optional[str] = None


class PharmacyPatch(BaseModel):
    name: Optional[str] = None
    fax: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    accepts_insurance: Optional[list[str]] = None
    device_names: Optional[list[str]] = None
    default_for_devices: Optional[list[str]] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


def _validate_fax(fax: Optional[str], field: str = "fax") -> None:
    """Reject obviously-bad fax numbers at the API boundary. A typo'd
    digit on a pharmacy fax is the canonical HIPAA-misdirection path —
    the entire enrollment form (demographics + insurance + card image)
    gets faxed to a stranger. Strip out non-digits and require at least
    10. (Fable LARC audit M3.)
    """
    if fax is None:
        return
    import re
    raw = fax.strip()
    if raw == "":
        return
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must contain at least 10 digits (got {raw!r})")


def _pharmacy_dict(p: LarcPharmacy) -> dict:
    return {
        "id": str(p.id), "name": p.name, "fax": p.fax, "phone": p.phone,
        "address": p.address, "accepts_insurance": p.accepts_insurance or [],
        "device_names": p.device_names or [],
        "default_for_devices": p.default_for_devices or [],
        "notes": p.notes,
    }


@router.get("/pharmacies")
def list_pharmacies(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
    device_name: Optional[str] = None,
):
    """List active pharmacies. If `device_name` is given, return only
    pharmacies whose `device_names` list contains it (or is empty —
    legacy rows with no device filter are assumed to serve everything)."""
    rows = (db.query(LarcPharmacy)
              .filter(LarcPharmacy.is_active.is_(True))
              .order_by(LarcPharmacy.name).all())
    if device_name:
        target = device_name.strip()
        rows = [p for p in rows if not (p.device_names) or target in (p.device_names or [])]
    return [_pharmacy_dict(p) for p in rows]


@router.patch("/pharmacies/{pharmacy_id}")
def patch_pharmacy(pharmacy_id: str, payload: PharmacyPatch,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    p = db.query(LarcPharmacy).filter(LarcPharmacy.id == pharmacy_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="pharmacy not found")
    data = payload.model_dump(exclude_unset=True)
    if "fax" in data:
        _validate_fax(data["fax"], "fax")
    before = {k: getattr(p, k) for k in data}
    for k, v in data.items():
        setattr(p, k, v)
    log_audit(db, actor=current_user.get("email") or "system",
              action="pharmacy_edited",
              summary=f"Edited pharmacy {p.name}: {list(data.keys())}",
              detail={"before": {k: str(v) if v is not None else None for k, v in before.items()},
                      "after":  {k: str(getattr(p, k)) if getattr(p, k) is not None else None for k in data}})
    db.commit(); db.refresh(p)
    return _pharmacy_dict(p)


@router.post("/pharmacies", status_code=201)
def create_pharmacy(payload: PharmacyIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    _validate_fax(payload.fax, "fax")
    p = LarcPharmacy(
        name=payload.name.strip(),
        fax=payload.fax, phone=payload.phone, address=payload.address,
        accepts_insurance=payload.accepts_insurance or [],
        device_names=payload.device_names or [],
        default_for_devices=payload.default_for_devices or [],
        notes=payload.notes,
    )
    db.add(p); db.commit(); db.refresh(p)
    return _pharmacy_dict(p)


# ─── Devices ────────────────────────────────────────────────────────

class DeviceIn(BaseModel):
    our_id: str
    device_type_id: str
    manufacturer_lot: Optional[str] = None
    manufacturer_serial: Optional[str] = None
    purchase_date: Optional[str] = None
    purchase_price: Optional[DollarAmount] = None
    expiration_date: Optional[str] = None
    location: str = "white_plains"
    notes: Optional[str] = None


ACTIVE_DEVICE_STATUSES = ["unassigned", "assigned", "received", "checked_out"]
# Complete enum of legal LarcDevice.status values — validates PATCH so
# typos like "Inserted"/"avaliable" can't silently drop devices from
# dashboard filters / on-hand counts / checkout gates.
# (Fable LARC audit M2.)
DEVICE_STATUSES = frozenset({
    "unassigned", "assigned", "checked_out", "inserted",
    "defective", "lost", "expired", "received", "returned",
})


@router.get("/devices")
def list_devices(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
    device_type_id: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    location: Optional[str] = None,
    ownership: Optional[str] = None,
    search: Optional[str] = None,
    active_only: bool = True,
    page: int = 1,
    per_page: int = 100,
):
    """List devices. By default `active_only=true` excludes terminal
    statuses (inserted/billed/lost/expired/defective/returned), which keeps
    historical archive rows from cluttering the working inventory view.
    Set `active_only=false` to see everything, or pass an explicit `status`
    to override."""
    q = db.query(LarcDevice).options(joinedload(LarcDevice.device_type))
    if device_type_id:
        q = q.filter(LarcDevice.device_type_id == device_type_id)
    if category:
        q = q.join(LarcDeviceType).filter(LarcDeviceType.category == category)
    if status:
        q = q.filter(LarcDevice.status == status)
        if status == "unassigned":
            # Defensive: exclude anything currently bound to an active
            # assignment, even if status drifted out of sync.
            bound = (db.query(LarcAssignment.device_id)
                       .filter(LarcAssignment.device_id.isnot(None),
                               LarcAssignment.is_active.is_(True)))
            q = q.filter(~LarcDevice.id.in_(bound.subquery().select()))
    elif active_only:
        q = q.filter(LarcDevice.status.in_(ACTIVE_DEVICE_STATUSES))
    if location:
        q = q.filter(LarcDevice.location == location)
    if ownership:
        q = q.filter(LarcDevice.ownership == ownership)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            LarcDevice.our_id.ilike(like),
            LarcDevice.manufacturer_lot.ilike(like),
            LarcDevice.manufacturer_serial.ilike(like),
        ))
    rows = q.order_by(LarcDevice.created_at.desc()).all()
    total = len(rows)
    paged = rows[(page - 1) * per_page : page * per_page]
    return {
        "total": total, "page": page, "per_page": per_page,
        "devices": [_device_dict(d) for d in paged],
    }


def _parse_date(s: Optional[str], field: str) -> Optional[_date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must be YYYY-MM-DD")


class BulkDeviceIn(BaseModel):
    devices: list[DeviceIn]


@router.post("/devices/bulk", status_code=201)
def create_devices_bulk(payload: BulkDeviceIn,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    """Add many devices in one shot — useful when receiving a shipment.
    Validates all rows up front; if any fails, nothing is committed."""
    if not payload.devices:
        raise HTTPException(status_code=422, detail="No devices provided")

    by = current_user.get("email") or "system"
    # First pass: validate everything and collect the rows to create
    pending = []
    seen_our_ids: set = set()
    for i, row in enumerate(payload.devices):
        if not row.our_id.strip():
            raise HTTPException(status_code=422, detail=f"Row {i+1}: our_id is required")
        if row.our_id.strip() in seen_our_ids:
            raise HTTPException(status_code=422, detail=f"Row {i+1}: our_id={row.our_id} repeated in batch")
        seen_our_ids.add(row.our_id.strip())
        dt = db.query(LarcDeviceType).filter(LarcDeviceType.id == row.device_type_id).first()
        if not dt:
            raise HTTPException(status_code=404, detail=f"Row {i+1}: device_type not found")
        if row.location not in LOCATIONS:
            raise HTTPException(status_code=422, detail=f"Row {i+1}: invalid location")
        existing = db.query(LarcDevice).filter(LarcDevice.our_id == row.our_id.strip()).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Row {i+1}: our_id={row.our_id} already exists")
        pending.append((row, dt))

    created = []
    for row, dt in pending:
        d = LarcDevice(
            our_id=row.our_id.strip(),
            device_type_id=dt.id,
            manufacturer_lot=row.manufacturer_lot,
            manufacturer_serial=row.manufacturer_serial,
            purchase_date=_parse_date(row.purchase_date, "purchase_date"),
            purchase_price=row.purchase_price,
            expiration_date=_parse_date(row.expiration_date, "expiration_date"),
            location=row.location,
            status="unassigned",
            notes=row.notes,
        )
        db.add(d); db.flush()
        log_audit(db, actor=by, action="device_added",
                  device=d,
                  summary=(f"Added {dt.name} #{d.our_id} (bulk import) at "
                           f"{LOCATION_LABELS.get(d.location, d.location)}"))
        created.append(d)
    db.commit()
    return {"created": len(created),
             "device_ids": [str(d.id) for d in created]}


@router.get("/devices/labels.pdf")
def device_labels_pdf(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
    ids: str = "",
):
    """Return a multi-page PDF with one label per device. Pass device IDs
    as a comma-separated list in `ids`."""
    from fastapi.responses import Response
    from app.services.larc.label import render_device_label
    import io
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import inch
    from pypdf import PdfReader, PdfWriter

    id_list = [s.strip() for s in (ids or "").split(",") if s.strip()]
    if not id_list:
        raise HTTPException(status_code=422, detail="ids query param required")

    devices = (db.query(LarcDevice)
                 .options(joinedload(LarcDevice.device_type))
                 .filter(LarcDevice.id.in_(id_list)).all())
    # Preserve requested order
    by_id = {str(d.id): d for d in devices}
    ordered = [by_id[i] for i in id_list if i in by_id]
    if not ordered:
        raise HTTPException(status_code=404, detail="No matching devices")

    # Merge each single-label PDF into one multi-page output
    writer = PdfWriter()
    for d in ordered:
        single = io.BytesIO(render_device_label(d))
        reader = PdfReader(single)
        for page in reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    pdf_bytes = out.getvalue()

    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="larc_labels_{len(ordered)}.pdf"'})


@router.post("/devices", status_code=201)
def create_device(payload: DeviceIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    dt = db.query(LarcDeviceType).filter(LarcDeviceType.id == payload.device_type_id).first()
    if not dt:
        raise HTTPException(status_code=404, detail="device_type not found")
    if payload.location not in LOCATIONS:
        raise HTTPException(status_code=422, detail=f"location must be one of {LOCATIONS}")

    # Enforce unique our_id
    existing = db.query(LarcDevice).filter(LarcDevice.our_id == payload.our_id.strip()).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Device with our_id={payload.our_id} already exists")

    d = LarcDevice(
        our_id=payload.our_id.strip(),
        device_type_id=dt.id,
        manufacturer_lot=payload.manufacturer_lot,
        manufacturer_serial=payload.manufacturer_serial,
        purchase_date=_parse_date(payload.purchase_date, "purchase_date"),
        purchase_price=payload.purchase_price,
        expiration_date=_parse_date(payload.expiration_date, "expiration_date"),
        location=payload.location,
        status="unassigned",
        notes=payload.notes,
    )
    db.add(d); db.flush()
    log_audit(db,
              actor=current_user.get("email") or "system",
              action="device_added",
              device=d,
              summary=f"Added {dt.name} #{d.our_id} (lot {d.manufacturer_lot or '—'}) "
                       f"at {LOCATION_LABELS.get(d.location, d.location)}",
              detail={"device_type": dt.name, "purchase_price": str(d.purchase_price) if d.purchase_price else None})
    db.commit(); db.refresh(d)
    return _device_dict(d)


@router.get("/devices/unallocated")
def list_unallocated_devices(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
    device_type_id: Optional[str] = None,
    category: Optional[str] = None,
    location: Optional[str] = None,
):
    """List devices that are in stock and not yet bound to a patient.
    Used by the Surgery module to pick an office-procedure device when
    scheduling a D&C (Bensta) or endometrial ablation (NovaSure).

    NB — this route MUST be registered before the /devices/{device_id}
    catch-all below or FastAPI will treat 'unallocated' as a device id
    and 404 the call. (Fable LARC audit C1.)
    """
    q = (db.query(LarcDevice)
           .options(joinedload(LarcDevice.device_type))
           .filter(LarcDevice.status == "unassigned"))
    if device_type_id:
        q = q.filter(LarcDevice.device_type_id == device_type_id)
    if category:
        q = q.join(LarcDeviceType).filter(LarcDeviceType.category == category)
    if location:
        q = q.filter(LarcDevice.location == location)
    rows = q.order_by(LarcDevice.expiration_date.asc().nullslast()).all()
    return [
        {
            "id": str(d.id), "our_id": d.our_id,
            "device_type_id": str(d.device_type_id),
            "device_type_name": d.device_type.name if d.device_type else None,
            "category": d.device_type.category if d.device_type else None,
            "manufacturer_lot": d.manufacturer_lot,
            "expiration_date": str(d.expiration_date) if d.expiration_date else None,
            "location": d.location,
            "location_label": LOCATION_LABELS.get(d.location, d.location),
        }
        for d in rows
    ]


@router.get("/devices/{device_id}")
def get_device(device_id: str,
                db: Session = Depends(get_db),
                current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    d = (db.query(LarcDevice)
           .options(joinedload(LarcDevice.device_type),
                    selectinload(LarcDevice.assignments).selectinload(LarcAssignment.milestones))
           .filter(LarcDevice.id == device_id).first())
    if not d:
        raise HTTPException(status_code=404, detail="device not found")
    return {
        **_device_dict(d),
        "assignments": [_assignment_dict(a, include_milestones=True) for a in (d.assignments or [])],
    }


class DevicePatch(BaseModel):
    our_id: Optional[str] = None
    manufacturer_lot: Optional[str] = None
    manufacturer_serial: Optional[str] = None
    purchase_date: Optional[str] = None
    purchase_price: Optional[DollarAmount] = None
    expiration_date: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class ChangeOwnershipIn(BaseModel):
    new_ownership: str           # 'patient_owned' | 'wwc_owned' | 'wwc_claimed'
    reason: str


@router.post("/devices/{device_id}/change-ownership")
def change_device_ownership(device_id: str,
                            payload: ChangeOwnershipIn,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    """Re-classify the ownership of a device — e.g. flip a patient-owned
    device to 'WWC Claimed' after the patient declined / didn't use it
    within the year-of-receipt window. Reason is required and the
    transition is recorded in the LARC audit log."""
    from app.models.larc import LARC_OWNERSHIP_VALUES
    if payload.new_ownership not in LARC_OWNERSHIP_VALUES:
        raise HTTPException(status_code=422,
                            detail=f"new_ownership must be one of {list(LARC_OWNERSHIP_VALUES)}")
    if not (payload.reason or "").strip():
        raise HTTPException(status_code=422, detail="reason is required")

    d = db.query(LarcDevice).options(joinedload(LarcDevice.device_type))\
          .filter(LarcDevice.id == device_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="device not found")
    old = d.ownership or "wwc_owned"
    if old == payload.new_ownership:
        raise HTTPException(status_code=409,
                            detail=f"Device is already classified as '{old}'.")
    d.ownership = payload.new_ownership
    by = current_user.get("email") or "system"
    log_audit(db, actor=by, action="ownership_changed",
              device=d,
              summary=(f"Ownership changed: "
                       f"{old.replace('_', ' ')} → "
                       f"{payload.new_ownership.replace('_', ' ')}. "
                       f"Reason: {payload.reason.strip()[:200]}"),
              detail={"from":   old,
                      "to":     payload.new_ownership,
                      "reason": payload.reason.strip()})

    # When flipping patient_owned → wwc_claimed, the patient whose device
    # was taken is owed a replacement. Push the active assignment's
    # patient onto the Owed list (idempotent — _push_to_owed dedupes by
    # chart + original assignment).
    if old == "patient_owned" and payload.new_ownership == "wwc_claimed":
        active = next((x for x in (d.assignments or [])
                       if x.is_active and x.chart_number), None)
        if active and active.device:
            from app.services.larc.sweeps import _push_to_owed
            _push_to_owed(
                db, active,
                expires_at=d.expiration_date,
                actor=by,
                summary=(f"Added to Owed list: original device claimed by WWC. "
                         f"Reason: {payload.reason.strip()[:160]}"),
            )

    db.commit(); db.refresh(d)
    return _device_dict(d)


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(device_id: str,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    """Hard-delete a LarcDevice row. Intended for pre-go-live inventory
    cleanup. Refuses if the device has ever been assigned to a patient —
    once a real assignment exists the row must stay for audit purposes;
    use 'return to manufacturer' instead."""
    d = db.query(LarcDevice).options(joinedload(LarcDevice.device_type))\
          .filter(LarcDevice.id == device_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="device not found")
    has_assignments = db.query(LarcAssignment).filter(
        LarcAssignment.device_id == d.id).count() > 0
    if has_assignments:
        raise HTTPException(
            status_code=409,
            detail=("This device has assignment history and can't be "
                    "deleted. Use 'return to manufacturer' or edit its "
                    "status instead."))
    # Belt + suspenders: the assignment-history check usually catches
    # this, but a stale or orphan LarcCheckout pointing at the device
    # would otherwise blow up at SQL commit (no ON DELETE cascade after
    # 633bcc6). Surface a clean 409 instead.
    has_checkouts = db.query(LarcCheckout).filter(
        LarcCheckout.device_id == d.id).count() > 0
    if has_checkouts:
        raise HTTPException(
            status_code=409,
            detail=("This device has checkout history and can't be "
                    "deleted. Use 'return to manufacturer' or edit its "
                    "status instead."))
    # Audit BEFORE removing the row so the event has a persistent record.
    type_name = d.device_type.name if d.device_type else None
    log_audit(db,
              actor=current_user.get("email") or "system",
              action="device_deleted",
              device=d,
              summary=f"Deleted device #{d.our_id}"
                      + (f" ({type_name})" if type_name else ""),
              detail={
                  "our_id":              d.our_id,
                  "device_type_id":      str(d.device_type_id) if d.device_type_id else None,
                  "device_type_name":    type_name,
                  "manufacturer_lot":    d.manufacturer_lot,
                  "manufacturer_serial": d.manufacturer_serial,
                  "expiration_date":     str(d.expiration_date) if d.expiration_date else None,
                  "purchase_date":       str(d.purchase_date) if d.purchase_date else None,
                  "status":              d.status,
                  "location":            d.location,
              })
    # NULL out the device_id FK on every audit row referencing this
    # device. Without this, the cascade fails because every device has
    # at least one device_added audit row at creation, so DELETE hits
    # a foreign-key violation on every device that was created through
    # the API. The audit rows' summary / detail still preserve our_id +
    # serial / lot for forensic queries. (Fable LARC audit H5.)
    from sqlalchemy import update as _sql_update
    db.execute(
        _sql_update(LarcAuditEvent)
          .where(LarcAuditEvent.device_id == d.id)
          .values(device_id=None)
    )
    db.delete(d)
    db.commit()
    return None


@router.patch("/devices/{device_id}")
def patch_device(device_id: str, payload: DevicePatch,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    d = db.query(LarcDevice).filter(LarcDevice.id == device_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="device not found")
    data = payload.model_dump(exclude_unset=True)
    if "purchase_date" in data:
        data["purchase_date"] = _parse_date(data["purchase_date"], "purchase_date")
    if "expiration_date" in data:
        data["expiration_date"] = _parse_date(data["expiration_date"], "expiration_date")
    if "location" in data and data["location"] not in LOCATIONS:
        raise HTTPException(status_code=422, detail=f"location must be one of {LOCATIONS}")
    if "status" in data and data["status"] not in DEVICE_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {sorted(DEVICE_STATUSES)}")
    before = {k: getattr(d, k) for k in data}
    for k, v in data.items():
        setattr(d, k, v)
    log_audit(db,
              actor=current_user.get("email") or "system",
              action="device_edited",
              device=d,
              detail={"before": {k: (str(v) if v is not None else None) for k, v in before.items()},
                      "after": {k: (str(getattr(d, k)) if getattr(d, k) is not None else None) for k in data}},
              summary=f"Edited device #{d.our_id} fields: {', '.join(data.keys())}")
    db.commit(); db.refresh(d)
    return _device_dict(d)


# ─── Assignments (Phase 1: list + create skeleton) ──────────────────

class AssignmentIn(BaseModel):
    device_id: Optional[str] = None         # null if pharmacy-order, set later on receipt
    chart_number: str
    patient_name: str                        # "Last, First" — required, kept for back-compat
    # Distinct name parts for pharmacy-enrollment-form prefill.
    patient_first_name:     Optional[str] = None
    patient_middle_initial: Optional[str] = None
    patient_last_name:      Optional[str] = None
    patient_dob: Optional[str] = None
    patient_email: Optional[str] = None
    patient_phone: Optional[str] = None
    patient_cell:  Optional[str] = None
    patient_address: Optional[str] = None
    patient_city:    Optional[str] = None
    patient_state:   Optional[str] = None
    patient_zip:     Optional[str] = None
    primary_insurance:   Optional[str] = None
    insurance_policy_no: Optional[str] = None
    insurance_group_no:  Optional[str] = None
    pharmacy_id: Optional[str] = None
    source_flow: str = "in_stock"           # in_stock | pharmacy_order
    device_type_id: Optional[str] = None    # required for pharmacy_order before a device exists
    notes: Optional[str] = None


@router.get("/assignments")
def list_assignments(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
    bucket: Optional[str] = None,
    status: Optional[str] = None,
    chart_number: Optional[str] = None,
    search: Optional[str] = None,
    linked_surgery_id: Optional[str] = None,
    include_completed: bool = False,
):
    q = (db.query(LarcAssignment)
           .options(joinedload(LarcAssignment.milestones),
                    joinedload(LarcAssignment.device).joinedload(LarcDevice.device_type))
           # Honor SoftDeleteMixin — rows scrubbed via admin/cleanup must
           # stay hidden. (Older read paths in this router are still
           # missing this filter and are tracked separately.)
           .filter(LarcAssignment.not_deleted()))
    if not include_completed:
        q = q.filter(LarcAssignment.status.notin_(["billed", "cancelled"]))
    if status:
        q = q.filter(LarcAssignment.status == status)
    if chart_number:
        q = q.filter(LarcAssignment.chart_number == chart_number)
    if linked_surgery_id:
        q = q.filter(LarcAssignment.linked_surgery_id == linked_surgery_id)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            LarcAssignment.patient_name.ilike(like),
            LarcAssignment.chart_number.ilike(like),
        ))
    rows = q.order_by(LarcAssignment.created_at.desc()).all()
    today = _date.today()
    if bucket:
        if bucket not in ALL_BUCKETS:
            raise HTTPException(status_code=422, detail=f"unknown bucket: {bucket}")
        rows = [a for a in rows if bucket in assignment_buckets(a, today)]
    return {
        "total": len(rows),
        "assignments": [_assignment_dict(a) for a in rows],
    }


@router.post("/assignments", status_code=201)
def create_assignment(payload: AssignmentIn,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    if payload.source_flow not in ("in_stock", "pharmacy_order"):
        raise HTTPException(status_code=422, detail="invalid source_flow")

    device: Optional[LarcDevice] = None
    if payload.device_id:
        device = db.query(LarcDevice).filter(LarcDevice.id == payload.device_id).first()
        if not device:
            raise HTTPException(status_code=404, detail="device not found")
        # Enforce 1-active-assignment-per-device rule
        active = (db.query(LarcAssignment)
                    .filter(LarcAssignment.device_id == device.id,
                            LarcAssignment.is_active.is_(True))
                    .first())
        if active:
            raise HTTPException(status_code=409,
                                detail=f"Device #{device.our_id} already has an active "
                                       f"assignment to {active.patient_name}")
    else:
        # No device_id provided. Pharmacy orders can start with no
        # device (the pharmacy ships one later). In-stock assignments
        # can also start with no device under the reserve-first flow —
        # benefits + payment happen first, then staff allocates a
        # specific unassigned WWC device via /allocate-device.
        if not payload.device_type_id:
            raise HTTPException(
                status_code=422,
                detail="device_type_id required when starting without a device_id",
            )

    # Default pharmacy lookup: if no pharmacy_id was provided AND this is
    # a pharmacy-order flow with a known device type, pick the pharmacy
    # marked as default_for_devices containing that device name.
    pharmacy_id = payload.pharmacy_id
    if (not pharmacy_id and payload.source_flow == "pharmacy_order"
            and payload.device_type_id):
        dt_row = (db.query(LarcDeviceType)
                    .filter(LarcDeviceType.id == payload.device_type_id)
                    .first())
        if dt_row:
            default_pharm = (db.query(LarcPharmacy)
                                .filter(LarcPharmacy.is_active.is_(True))
                                .all())
            for p in default_pharm:
                if dt_row.name in (p.default_for_devices or []):
                    pharmacy_id = str(p.id)
                    break

    a = LarcAssignment(
        device_id=device.id if device else None,
        # Pin device_type at creation — required for pharmacy_order
        # assignments without a device so the enrollment sender can
        # pick the right template before receive-device.
        device_type_id=(device.device_type_id if device
                          else payload.device_type_id),
        chart_number=payload.chart_number.strip(),
        patient_name=payload.patient_name.strip(),
        patient_first_name=(payload.patient_first_name or "").strip() or None,
        patient_middle_initial=(payload.patient_middle_initial or "").strip() or None,
        patient_last_name=(payload.patient_last_name or "").strip() or None,
        patient_dob=_parse_date(payload.patient_dob, "patient_dob"),
        patient_email=payload.patient_email,
        patient_phone=payload.patient_phone,
        patient_cell=payload.patient_cell,
        patient_address=payload.patient_address,
        patient_city=payload.patient_city,
        patient_state=payload.patient_state,
        patient_zip=payload.patient_zip,
        primary_insurance=payload.primary_insurance,
        insurance_policy_no=payload.insurance_policy_no,
        insurance_group_no=payload.insurance_group_no,
        pharmacy_id=pharmacy_id,
        source_flow=payload.source_flow,
        status="new",
        is_active=True,
        notes=payload.notes,
        created_by=current_user.get("email"),
    )
    db.add(a); db.flush()
    spawn_milestones(db, a)
    if device:
        device.status = "assigned"

    log_audit(db,
              actor=current_user.get("email") or "system",
              action="assignment_created",
              device=device,
              assignment=a,
              summary=(f"Started {payload.source_flow.replace('_', ' ')} assignment for "
                        f"{a.patient_name} (chart {a.chart_number})"),
              detail={"source_flow": payload.source_flow})
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


@router.get("/assignments/{assignment_id}")
def get_assignment(assignment_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    a = (db.query(LarcAssignment)
           .options(joinedload(LarcAssignment.milestones),
                    joinedload(LarcAssignment.device).joinedload(LarcDevice.device_type))
           .filter(LarcAssignment.id == assignment_id).first())
    if not a:
        raise HTTPException(status_code=404, detail="assignment not found")

    # Self-heal: legacy assignments may have been created before
    # spawn_milestones existed, or via a path that skipped it. Without
    # milestones the detail page renders only the Benefits Calculator
    # and the user has no way to step through the workflow. Spawn the
    # catalog the first time anyone opens the page; spawn_milestones is
    # idempotent (`if assignment.milestones: return`) so this is safe.
    # Race window between two simultaneous GETs is microseconds; the
    # idempotency check inside spawn_milestones is the safety net.
    if not a.milestones:
        spawn_milestones(db, a)
        db.commit()
        db.refresh(a)

    # PHI access logging (Fable LARC audit M6). The assignment dict
    # exposes DOB, demographics, insurance policy info; central audit
    # makes those reads queryable for misuse investigation.
    log_action(
        db,
        action="CHART_VIEW",
        resource_type="larc_assignment",
        resource_id=str(a.id),
        patient_id=a.chart_number or None,
        user_id=(current_user.get("email") or "").lower() or None,
        user_name=current_user.get("name") or current_user.get("email"),
        description=f"Viewed LARC assignment for {a.patient_name}",
    )
    return _assignment_dict(a, include_milestones=True)


# ─── Milestone helpers + endpoints ──────────────────────────────────

def _get_milestone(a: LarcAssignment, kind: str) -> Optional[LarcMilestone]:
    return next((m for m in (a.milestones or []) if m.kind == kind), None)


def _mark_milestone(a: LarcAssignment, kind: str, *, status: str, by: str,
                     notes: Optional[str] = None) -> Optional[LarcMilestone]:
    m = _get_milestone(a, kind)
    if not m:
        return None
    m.status = status
    if status in ("done", "skipped", "not_applicable"):
        m.completed_at = now_utc_naive()
        m.completed_by = by
    elif status == "in_progress":
        m.started_at = m.started_at or now_utc_naive()
    if notes is not None:
        m.notes = notes
    return m


def _load_assignment(db: Session, assignment_id: str) -> LarcAssignment:
    a = (db.query(LarcAssignment)
           .options(joinedload(LarcAssignment.milestones),
                    joinedload(LarcAssignment.device).joinedload(LarcDevice.device_type))
           .filter(LarcAssignment.id == assignment_id).first())
    if not a:
        raise HTTPException(status_code=404, detail="assignment not found")
    return a


def _block_if_closed_or_billed(a: LarcAssignment, action: str) -> None:
    """Reject mutations on assignments that have already been billed or
    explicitly closed. Used by every status-mutating endpoint to prevent
    silent rewrites of a finished row (re-allocating a billed device,
    re-consuming, re-checking-out a cancelled assignment, etc.). The
    audit explicitly called this gap out (#7 in the LARC audit).
    Operators who genuinely need to amend a closed row must go through
    an explicit reopen path."""
    if a.status == "billed":
        raise HTTPException(status_code=409,
            detail=f"cannot {action} — assignment has already been billed; "
                   "use the reopen / amend flow if the claim must be corrected")
    if a.is_active is False:
        raise HTTPException(status_code=409,
            detail=f"cannot {action} — assignment is closed (status={a.status}); "
                   "reopen it first if you need to make this change")


class BenefitsIn(BaseModel):
    primary_insurance: Optional[str] = None
    # Calculator inputs — all optional; missing → treated as 0 in the math
    allowed_amount:   Optional[DollarAmount]  = None
    deductible:       Optional[DollarAmount]  = None
    deductible_met:   Optional[DollarAmount]  = None
    copay:            Optional[DollarAmount]  = None
    coinsurance_pct:  Optional[PercentAmount] = None
    oop_max:          Optional[DollarAmount]  = None
    oop_met:          Optional[DollarAmount]  = None
    # Legacy direct override — if provided, replaces the computed value.
    patient_responsibility: Optional[DollarAmount] = None
    notes:            Optional[str] = None
    save:             bool = True   # False = preview-only


def _calc_patient_responsibility(*, allowed_amount: float, deductible: float,
                                   deductible_met: float, copay: float,
                                   coinsurance_pct: float,
                                   oop_max: float, oop_met: float) -> dict:
    """Standard health-plan math. Kept in sync with the Surgery calculator
    in app/routers/surgery.py — same formula, same field names."""
    deductible_remaining = max(0.0, deductible - deductible_met)
    oop_remaining = max(0.0, oop_max - oop_met) if oop_max > 0 else float("inf")

    deductible_portion = min(allowed_amount, deductible_remaining)
    after_deductible   = allowed_amount - deductible_portion

    coins_rate = coinsurance_pct / 100.0
    coinsurance_portion = round(after_deductible * coins_rate, 2)

    raw_responsibility    = deductible_portion + coinsurance_portion + copay
    capped_responsibility = round(min(raw_responsibility, oop_remaining), 2)

    return {
        "deductible_remaining": round(deductible_remaining, 2),
        "deductible_portion":   round(deductible_portion, 2),
        "after_deductible":     round(after_deductible, 2),
        "coinsurance_portion":  coinsurance_portion,
        "copay_portion":        round(copay, 2),
        "oop_remaining":        (round(oop_remaining, 2) if oop_remaining != float("inf") else None),
        "raw_responsibility":   round(raw_responsibility, 2),
        "patient_responsibility": capped_responsibility,
        "capped_by_oop_max":    raw_responsibility > oop_remaining,
    }


@router.post("/assignments/{assignment_id}/benefits")
def record_benefits(assignment_id: str, payload: BenefitsIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Record insurance benefits via the calculator (same math as Surgery).
    When save=True, persists inputs, sets patient_responsibility from the
    calculator, marks the benefits_verified milestone done, and stamps
    benefits_verified_at."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="record benefits")

    # Coalesce inputs: payload wins, then existing assignment value, then 0
    def _g(field: str) -> float:
        v = getattr(payload, field, None)
        if v is not None:
            return float(v)
        existing = getattr(a, field, None)
        return float(existing or 0)

    breakdown = _calc_patient_responsibility(
        allowed_amount   = _g("allowed_amount"),
        deductible       = _g("deductible"),
        deductible_met   = _g("deductible_met"),
        copay            = _g("copay"),
        coinsurance_pct  = _g("coinsurance_pct"),
        oop_max          = _g("oop_max"),
        oop_met          = _g("oop_met"),
    )

    if not payload.save:
        return {"breakdown": breakdown, "saved": False,
                 "patient_responsibility": breakdown["patient_responsibility"]}

    if payload.primary_insurance is not None:
        a.primary_insurance = payload.primary_insurance
    for field in ("allowed_amount", "deductible", "deductible_met", "copay",
                   "coinsurance_pct", "oop_max", "oop_met"):
        v = getattr(payload, field, None)
        if v is not None:
            setattr(a, field, v)

    # Direct override takes precedence over the calculator if explicitly sent
    if payload.patient_responsibility is not None:
        a.patient_responsibility = payload.patient_responsibility
    else:
        a.patient_responsibility = breakdown["patient_responsibility"]
    a.benefits_verified_at = _date.today()

    by = current_user.get("email") or "system"
    _mark_milestone(a, "benefits_verified", status="done", by=by, notes=payload.notes)
    if a.status == "new":
        a.status = "in_progress"
    log_audit(db, actor=by, action="benefits_verified",
              device=a.device, assignment=a,
              summary=(f"Benefits verified for {a.patient_name}"
                       f" — pt responsibility ${a.patient_responsibility}"),
              detail={"patient_responsibility": str(a.patient_responsibility),
                       "breakdown": breakdown})
    db.commit(); db.refresh(a)
    return {
        "assignment": _assignment_dict(a, include_milestones=True),
        "breakdown": breakdown,
        "saved": True,
        "patient_responsibility": breakdown["patient_responsibility"],
    }


class ToggleConfirmIn(BaseModel):
    confirmed: bool = True
    notes: Optional[str] = None


@router.post("/assignments/{assignment_id}/responsibility-in-modmed")
def toggle_responsibility_in_modmed(
    assignment_id: str, payload: ToggleConfirmIn = ToggleConfirmIn(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Mark that the patient's out-of-pocket has been entered in ModMed.
    No ModMed integration — purely a staff checkbox."""
    a = _load_assignment(db, assignment_id)
    by = current_user.get("email") or "system"
    if payload.confirmed:
        a.patient_responsibility_in_modmed_at = now_utc_naive()
        a.patient_responsibility_in_modmed_by = by
        _mark_milestone(a, "patient_responsibility_modmed", status="done",
                         by=by, notes=payload.notes)
        log_audit(db, actor=by, action="modmed_responsibility_recorded",
                  device=a.device, assignment=a,
                  summary=f"Patient responsibility entered in ModMed for {a.patient_name}")
    else:
        a.patient_responsibility_in_modmed_at = None
        a.patient_responsibility_in_modmed_by = None
        _mark_milestone(a, "patient_responsibility_modmed", status="pending", by=by)
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


class NotifyIn(BaseModel):
    message_body: Optional[str] = None    # what the staff sent on Klara (kept for the audit)


@router.post("/assignments/{assignment_id}/notify")
def mark_patient_notified(
    assignment_id: str, payload: NotifyIn = NotifyIn(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Mark that the patient was notified via Klara to schedule their
    insertion appointment."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="mark patient notified")
    by = current_user.get("email") or "system"
    a.patient_notified_at = now_utc_naive()
    _mark_milestone(a, "patient_notified", status="done", by=by)
    log_audit(db, actor=by, action="patient_notified",
              device=a.device, assignment=a,
              summary=f"Sent Klara to {a.patient_name} to schedule insertion",
              detail={"message_body": payload.message_body} if payload.message_body else None)
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


class ApptScheduledIn(BaseModel):
    appt_date: str    # YYYY-MM-DD


@router.post("/assignments/{assignment_id}/schedule-appt")
def schedule_appt(assignment_id: str, payload: ApptScheduledIn,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Record the patient's insertion appointment date."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="schedule appointment")
    by = current_user.get("email") or "system"
    try:
        a.appt_date = datetime.strptime(payload.appt_date[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="appt_date must be YYYY-MM-DD")
    a.appt_scheduled_at = now_utc_naive()
    _mark_milestone(a, "appt_scheduled", status="done", by=by)
    log_audit(db, actor=by, action="appt_scheduled",
              device=a.device, assignment=a,
              summary=f"Insertion appt set for {a.patient_name}: {a.appt_date}")
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


class OutcomeIn(BaseModel):
    outcome: str   # inserted | failed_unused | failed_used | patient_no_show |
                   # patient_canceled | office_canceled | lost | other
    notes: Optional[str] = None
    loss_value: Optional[DollarAmount] = None


VALID_OUTCOMES = {
    "inserted", "failed_unused", "failed_used", "patient_no_show",
    "patient_canceled", "office_canceled", "lost", "other",
}


@router.post("/assignments/{assignment_id}/outcome")
def record_outcome(assignment_id: str, payload: OutcomeIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Record the insertion-day outcome. Drives device status:
      inserted        → device.status='inserted', advances to 'billed' milestone
      failed_used     → device.status='defective' (likely manufacturer return)
      failed_unused   → device.status='unassigned' (back to stock)
      patient_*       → assignment status flagged; device stays assigned
      lost            → device.status='lost'
    'other' requires notes."""
    a = _load_assignment(db, assignment_id)
    if payload.outcome not in VALID_OUTCOMES:
        raise HTTPException(status_code=422, detail=f"outcome must be one of {sorted(VALID_OUTCOMES)}")
    if payload.outcome == "other" and not (payload.notes and payload.notes.strip()):
        raise HTTPException(status_code=422, detail="notes required when outcome='other'")

    _block_if_closed_or_billed(a, action="change outcome")

    by = current_user.get("email") or "system"
    a.failure_reason = payload.outcome if payload.outcome != "inserted" else None
    a.failure_notes = payload.notes
    now = now_utc_naive()
    prev_assignment_status = a.status
    prev_device_status = a.device.status if a.device else None

    if payload.outcome == "inserted":
        a.inserted_at = now
        a.inserted_by = by
        a.status = "inserted"
        _mark_milestone(a, "device_inserted", status="done", by=by)
        if a.device:
            a.device.status = "inserted"
    elif payload.outcome == "failed_unused":
        a.status = "failed_unused"
        if a.device:
            # Return to stock pool
            a.device.status = "unassigned"
        a.is_active = False
    elif payload.outcome == "failed_used":
        a.status = "failed_used"
        if a.device:
            a.device.status = "defective"
        # Stays active until replacement chain is started
    elif payload.outcome in ("patient_no_show", "patient_canceled", "office_canceled"):
        a.status = payload.outcome
        if a.device:
            # Device returns to stock; new appointment / new assignment may follow
            a.device.status = "unassigned"
        a.is_active = False
        # Void any still-live BoldSign enrollment envelopes — otherwise
        # a patient who signs days later auto-faxes the order to the
        # pharmacy for an assignment that no longer applies. Best-effort;
        # failures are recorded on the envelope row for the sweep job.
        # (Fable LARC audit C2.)
        from app.services.larc.enrollment_sender import (
            void_live_envelopes_for_assignment,
        )
        try:
            voided = void_live_envelopes_for_assignment(
                db, a, reason=f"Assignment {payload.outcome}", actor_email=by)
            for env in voided:
                log_audit(db, actor=by, action="envelope_voided",
                          device=a.device, assignment=a,
                          summary=(f"Voided enrollment envelope "
                                   f"{(env.boldsign_envelope_id or '')[:8]}… "
                                   f"on {payload.outcome}"),
                          detail={"envelope_id": str(env.id),
                                  "new_status": env.status,
                                  "boldsign_envelope_id": env.boldsign_envelope_id,
                                  "last_fax_error": env.last_fax_error})
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "envelope void on outcome %s failed for assignment %s: %s",
                payload.outcome, a.id, exc)
    elif payload.outcome == "lost":
        if a.device:
            a.device.status = "lost"
        a.status = "lost"   # mirror the outcome so bucket filters find it
    elif payload.outcome == "other":
        a.status = "other"

    # Write the outcome onto the open LarcCheckout row so end_of_day_report
    # can actually reconcile against the physical cabinet. Without this
    # the EOD report renders c.outcome=None for every checkout, forever,
    # and the cabinet-match audit is meaningless. (Fable LARC audit H4.)
    open_checkout = (db.query(LarcCheckout)
                       .filter(LarcCheckout.assignment_id == a.id,
                               LarcCheckout.outcome.is_(None),
                               LarcCheckout.approval_status.in_(
                                   ["approved", "auto_approved"]))
                       .order_by(LarcCheckout.created_at.desc())
                       .first())
    if open_checkout:
        from decimal import Decimal as _Dec
        open_checkout.outcome = payload.outcome
        if payload.notes:
            open_checkout.outcome_notes = payload.notes
        if payload.loss_value is not None:
            try:
                open_checkout.outcome_loss_value = _Dec(str(payload.loss_value))
            except Exception:
                pass

    log_audit(db, actor=by, action="outcome_recorded",
              device=a.device, assignment=a,
              summary=f"{a.patient_name}: outcome={payload.outcome}" +
                       (f" — {payload.notes[:60]}" if payload.notes else ""),
              detail={"outcome": payload.outcome, "notes": payload.notes,
                       "loss_value": payload.loss_value,
                       "checkout_id": (str(open_checkout.id)
                                        if open_checkout else None)})

    # Cross-module state-transition audit
    from app.services.state_audit import log_state_transition
    log_state_transition(db,
        entity_type="larc_assignment",
        entity_id=a.id,
        action="outcome_recorded",
        actor=by,
        before=prev_assignment_status,
        after=a.status,
        summary=f"{a.patient_name}: outcome={payload.outcome}",
        detail={"outcome": payload.outcome,
                "device_status_before": prev_device_status,
                "device_status_after": a.device.status if a.device else None})

    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


# ─── Pharmacy-order flow ───────────────────────────────────────────

class EnrollmentSendIn(BaseModel):
    # The two per-send checkboxes on the Nexplanon form. Each device-
    # specific form may expose its own set in Phase 5 — keep this loose.
    dispense: bool = False
    provider_contact_preference: bool = False


@router.post("/assignments/{assignment_id}/send-enrollment")
def send_enrollment(assignment_id: str, payload: EnrollmentSendIn = EnrollmentSendIn(),
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Send the device-type's BoldSign enrollment envelope. Validates
    prerequisites in the sender and surfaces actionable 409s — won't
    burn an envelope on missing patient_email / unwired template."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="send enrollment envelope")
    if a.source_flow != "pharmacy_order":
        raise HTTPException(status_code=409,
                            detail="Enrollment only applies to pharmacy_order flow")
    by = current_user.get("email") or "system"

    from app.services.larc.enrollment_sender import (
        send_enrollment_envelope, LarcEnrollmentError,
    )
    from sqlalchemy.exc import IntegrityError
    try:
        env = send_enrollment_envelope(
            db, a, sent_by_email=by,
            dispense=payload.dispense,
            provider_contact_preference=payload.provider_contact_preference,
        )
    except LarcEnrollmentError as exc:
        # Sender raises these for missing prerequisites or BoldSign errors.
        raise HTTPException(status_code=409, detail=str(exc))
    except IntegrityError:
        # ix_larc_envelope_live_unique fired — a concurrent send raced
        # past the app-level guard. Surface a clean 409. (Fable C2.)
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=("Another envelope for this assignment was sent "
                    "concurrently. Refresh to see the live envelope."))

    _mark_milestone(a, "enrollment_sent", status="done", by=by)
    log_audit(db, actor=by, action="enrollment_sent",
              device=a.device, assignment=a,
              summary=(f"BoldSign enrollment envelope sent to {a.patient_name} "
                       f"({(env.boldsign_envelope_id or '')[:8]}…)"),
              detail={"boldsign_envelope_id": env.boldsign_envelope_id,
                      "template_id": env.boldsign_template_id})
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


@router.post("/envelopes/{envelope_id}/refax")
def refax_envelope(envelope_id: str,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Manually retry the auto-fax of a completed enrollment envelope.

    Use case: the webhook fired the fax and RingCentral rejected it
    (busy number, bad PDF). Staff fixes the pharmacy fax number then
    hits this to retry without having to void + resend the BoldSign
    envelope."""
    env = (db.query(LarcEnrollmentEnvelope)
             .filter(LarcEnrollmentEnvelope.id == envelope_id)
             .first())
    if env is None:
        raise HTTPException(status_code=404, detail="envelope not found")
    if not env.signed_at:
        raise HTTPException(status_code=409,
                            detail="envelope is not yet fully signed — nothing to fax")
    by = current_user.get("email") or "system"
    from app.services.larc.pharmacy_fax import fax_envelope
    result = fax_envelope(db, env, by_email=by, force=True)
    if not result.get("ok"):
        # Soft-fail with the persisted error rather than 500 — the row
        # already has fax_status=fax_failed + last_fax_error.
        raise HTTPException(status_code=502, detail=result.get("error"))
    return result


_INSURANCE_CARD_ALLOWED_MIME = {
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/heic", "image/heif", "application/pdf",
}
_INSURANCE_CARD_MAX_BYTES = 10 * 1024 * 1024   # 10 MB

# Magic-byte sniff so a caller can't pass a PDF labeled as image/png and
# then re-fetch it inline as text/html. Maps the *trusted* media type to
# the byte prefixes we accept.
_INSURANCE_CARD_MAGIC = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/jpg":  (b"\xff\xd8\xff",),
    "image/png":  (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),                       # full check inspects bytes 8-12
    "image/heic": (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftyphevx"),
    "image/heif": (b"ftypmif1", b"ftypmsf1", b"ftypheic", b"ftypheix"),
    "application/pdf": (b"%PDF-",),
}


def _sniff_insurance_card(body: bytes, declared_type: str) -> bool:
    """Return True iff the file's first bytes match the declared MIME."""
    t = (declared_type or "").lower()
    if t == "image/webp":
        return len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    if t in ("image/heic", "image/heif"):
        # HEIC/HEIF: brand is at offset 4 (4-byte size prefix), check 4–12
        return len(body) >= 12 and any(b in body[4:32] for b in _INSURANCE_CARD_MAGIC[t])
    for magic in _INSURANCE_CARD_MAGIC.get(t, ()):
        if body.startswith(magic):
            return True
    return False


@router.post("/assignments/{assignment_id}/insurance-card", status_code=201)
async def upload_insurance_card(
    assignment_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK)),
):
    """Upload (or replace) the patient's insurance card image for this
    assignment. Stored via the shared blob service so it lands in GCS
    on Cloud Run; legacy local backend works the same.

    The image gets attached to the BoldSign envelope as a supplemental
    file at send time (sender reads insurance_card_key and pulls bytes
    via storage.read_blob)."""
    a = _load_assignment(db, assignment_id)
    declared = (file.content_type or "").lower()
    if declared not in _INSURANCE_CARD_ALLOWED_MIME:
        raise HTTPException(status_code=415,
            detail=f"insurance card must be one of: "
                   f"{', '.join(sorted(_INSURANCE_CARD_ALLOWED_MIME))}")
    body = await file.read()
    if not body:
        raise HTTPException(status_code=422, detail="empty file")
    if len(body) > _INSURANCE_CARD_MAX_BYTES:
        raise HTTPException(status_code=413,
            detail=f"file exceeds {_INSURANCE_CARD_MAX_BYTES // (1024*1024)} MB limit")
    if not _sniff_insurance_card(body, declared):
        raise HTTPException(status_code=415,
            detail="file contents do not match the declared image/PDF type")
    from app.services.storage import save_blob
    key = save_blob(prefix="larc/insurance-cards", body=body,
                    filename=file.filename or "insurance_card")
    a.insurance_card_key = key
    a.insurance_card_filename = file.filename
    a.insurance_card_content_type = declared
    log_audit(db, actor=current_user.get("email") or "system",
              action="insurance_card_uploaded",
              device=a.device, assignment=a,
              summary=f"Uploaded insurance card for {a.patient_name} ({file.filename})")
    db.commit(); db.refresh(a)
    return {"key": key, "filename": file.filename}


@router.get("/assignments/{assignment_id}/insurance-card")
def download_insurance_card(
    assignment_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK)),
):
    a = _load_assignment(db, assignment_id)
    if not a.insurance_card_key:
        raise HTTPException(status_code=404, detail="no insurance card on file")
    # PHI access logging: HIPAA requires we record every fetch of a
    # patient's insurance card, not just the upload.
    log_audit(db, actor=current_user.get("email") or "system",
              action="insurance_card_viewed",
              device=a.device, assignment=a,
              summary=f"Viewed insurance card for {a.patient_name}")
    db.commit()
    # Pin the served Content-Type to the upload-time allow-list so a
    # caller-controlled MIME can't be replayed as text/html for XSS.
    stored = (a.insurance_card_content_type or "").lower()
    safe_type = stored if stored in _INSURANCE_CARD_ALLOWED_MIME else "application/octet-stream"
    from app.services.storage import serve_blob
    from app.config import settings
    local_root = settings.documents_local_root
    # Defense-in-depth: the key is UUID-derived today so path-traversal
    # isn't reachable via current code paths, but normalize and confirm
    # the resolved path stays under local_root so any future code path
    # that ever stored a user-influenced key can't escape the docs
    # directory.
    raw_target = os.path.join(local_root, a.insurance_card_key)
    target_real = os.path.realpath(raw_target)
    root_real = os.path.realpath(local_root)
    if target_real != root_real and not target_real.startswith(root_real + os.sep):
        raise HTTPException(status_code=400,
            detail="invalid insurance card storage key")
    return serve_blob(
        local_path=target_real,
        gcs_object=a.insurance_card_key,
        media_type=safe_type,
        filename=a.insurance_card_filename or "insurance_card",
        disposition="inline",
    )


class PaymentIn(BaseModel):
    amount: Optional[DollarAmount] = None   # dollars; None = no amount recorded
    notes:  Optional[str] = None


@router.post("/assignments/{assignment_id}/payment-received")
def record_payment(assignment_id: str,
                    payload: PaymentIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Mark the patient's responsibility as paid. Required (along with
    benefits-verified) before an unassigned WWC device can be allocated
    from inventory."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="record payment")
    by = current_user.get("email") or "system"
    a.patient_paid_at = now_utc_naive()
    a.patient_paid_by = by
    if payload.amount is not None:
        a.patient_paid_amount = payload.amount
    log_audit(db, actor=by, action="patient_payment_received",
              device=a.device, assignment=a,
              summary=f"Patient payment received for {a.patient_name}"
                       + (f" (${payload.amount:.2f})" if payload.amount else ""),
              detail={"amount": payload.amount, "notes": payload.notes})
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


class AllocateDeviceIn(BaseModel):
    device_id: str


@router.post("/assignments/{assignment_id}/allocate-device")
def allocate_device(assignment_id: str,
                     payload: AllocateDeviceIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Bind a specific unassigned WWC device to this assignment. Only
    valid when:
      - source_flow == 'in_stock'
      - assignment.device_id is currently NULL
      - benefits are verified AND patient has paid
      - the device exists, is unassigned, and matches the assignment's
        device_type_id

    Returns 409 if any gate fails so the UI can surface the reason."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="allocate a device")
    if a.source_flow != "in_stock":
        raise HTTPException(status_code=409,
                            detail="Allocation only applies to in-stock assignments")
    if a.device_id:
        raise HTTPException(status_code=409,
                            detail=f"Already allocated device #{a.device.our_id if a.device else '?'}")
    if not a.benefits_verified_at:
        raise HTTPException(status_code=409,
                            detail="Benefits must be verified before allocating a device")
    if not a.patient_paid_at:
        raise HTTPException(status_code=409,
                            detail="Patient payment must be recorded before allocating a device")

    d = db.query(LarcDevice).filter(LarcDevice.id == payload.device_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="device not found")
    if d.status != "unassigned":
        raise HTTPException(status_code=409,
                            detail=f"Device is in status {d.status!r}; only 'unassigned' devices can be allocated")
    if a.device_type_id and d.device_type_id != a.device_type_id:
        raise HTTPException(
            status_code=409,
            detail=f"Device type mismatch — assignment is for type "
                   f"{a.device_type_id}, device is {d.device_type_id}",
        )

    # Atomic claim: only one allocation succeeds when two staff try to
    # bind the same device. Without this, the previous read-then-mutate
    # let both threads pass d.status=='unassigned' and both set
    # d.status='assigned' (last write wins) → corrupted chain of custody
    # for an implantable device. Postgres' partial unique
    # ix_larc_assignment_active_unique helps at the assignment side, but
    # the device-side race needed its own conditional UPDATE.
    # (Fable LARC audit C3.)
    from sqlalchemy import update as _sql_update
    claimed = db.execute(
        _sql_update(LarcDevice)
          .where(LarcDevice.id == d.id, LarcDevice.status == "unassigned")
          .values(status="assigned")
    ).rowcount
    if not claimed:
        db.refresh(d)
        raise HTTPException(
            status_code=409,
            detail=(f"Device is in status {d.status!r} — was claimed by "
                    "a concurrent allocation. Refresh the device list."))
    db.refresh(d)

    # Bind
    a.device_id = d.id
    by = current_user.get("email") or "system"
    log_audit(db, actor=by, action="device_allocated",
              device=d, assignment=a,
              summary=f"Allocated device #{d.our_id} to {a.patient_name} "
                       f"(post benefits + payment)",
              detail={"device_our_id": d.our_id, "lot": d.manufacturer_lot})
    from sqlalchemy.exc import IntegrityError
    try:
        db.commit()
    except IntegrityError:
        # ix_larc_assignment_active_unique fired — this device is bound
        # to another active assignment. Surface a clean 409.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=("Device is already bound to another active assignment. "
                    "Refresh and try again."))
    db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


class InsertingProviderIn(BaseModel):
    email: Optional[str] = None       # Empty string / null clears the override
    name:  Optional[str] = None
    npi:   Optional[str] = None


@router.post("/assignments/{assignment_id}/inserting-provider")
def set_inserting_provider(
    assignment_id: str, payload: InsertingProviderIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK)),
):
    """Set the per-assignment inserting-provider override. Each field is
    optional and overridden independently — empty string clears that
    one field (falls back to the practice-wide provider settings when
    the BoldSign envelope is sent)."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="set inserting provider")
    by = current_user.get("email") or "system"
    before = {
        "email": a.inserting_provider_email,
        "name":  a.inserting_provider_name,
        "npi":   a.inserting_provider_npi,
    }
    if payload.email is not None:
        a.inserting_provider_email = (payload.email or "").strip() or None
    if payload.name is not None:
        a.inserting_provider_name  = (payload.name  or "").strip() or None
    if payload.npi is not None:
        a.inserting_provider_npi   = (payload.npi   or "").strip() or None
    log_audit(db, actor=by, action="inserting_provider_set",
              device=a.device, assignment=a,
              summary=f"Inserting provider override updated for {a.patient_name}",
              detail={"before": before, "after": {
                  "email": a.inserting_provider_email,
                  "name":  a.inserting_provider_name,
                  "npi":   a.inserting_provider_npi,
              }})
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


@router.post("/assignments/{assignment_id}/app")
def set_app(assignment_id: str, payload: InsertingProviderIn,
             db: Session = Depends(get_db),
             current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Set the per-assignment APP (Advanced Practice Provider) override.
    Same shape as /inserting-provider — empty string clears one field,
    falling back to PracticeConfig app_name/app_npi."""
    a = _load_assignment(db, assignment_id)
    by = current_user.get("email") or "system"
    before = {"email": a.app_email, "name": a.app_name, "npi": a.app_npi}
    if payload.email is not None:
        a.app_email = (payload.email or "").strip() or None
    if payload.name is not None:
        a.app_name  = (payload.name  or "").strip() or None
    if payload.npi is not None:
        a.app_npi   = (payload.npi   or "").strip() or None
    log_audit(db, actor=by, action="app_set",
              device=a.device, assignment=a,
              summary=f"APP override updated for {a.patient_name}",
              detail={"before": before, "after": {
                  "email": a.app_email, "name": a.app_name, "npi": a.app_npi,
              }})
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


@router.post("/assignments/{assignment_id}/enrollment-signed")
def mark_enrollment_signed(assignment_id: str,
                            payload: ToggleConfirmIn = ToggleConfirmIn(),
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Manual mark that the enrollment form is signed and back in hand."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="mark enrollment signed")
    by = current_user.get("email") or "system"
    if payload.confirmed:
        a.enrollment_signed_at = now_utc_naive()
        _mark_milestone(a, "enrollment_signed", status="done", by=by, notes=payload.notes)
        log_audit(db, actor=by, action="enrollment_signed",
                  device=a.device, assignment=a,
                  summary=f"Enrollment form signed by {a.patient_name}")
    else:
        a.enrollment_signed_at = None
        _mark_milestone(a, "enrollment_signed", status="pending", by=by)
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


class FaxPharmacyIn(BaseModel):
    pharmacy_id: Optional[str] = None
    notes: Optional[str] = None


@router.post("/assignments/{assignment_id}/fax-pharmacy")
def fax_pharmacy(assignment_id: str, payload: FaxPharmacyIn = FaxPharmacyIn(),
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Record that the order was faxed to the pharmacy. Starts the
    2-week SLA clock — overdue orders surface on the dashboard."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="fax pharmacy order")
    if a.source_flow != "pharmacy_order":
        raise HTTPException(status_code=409, detail="Only pharmacy_order assignments")
    by = current_user.get("email") or "system"
    # Verify the pharmacy actually exists, is active, and has a fax
    # number on file before stamping request_faxed_at and starting the
    # SLA clock. Without these checks fax_pharmacy used to record a fax
    # that never went anywhere (PHI misdirection if a stale row had the
    # wrong number). (Fable LARC audit M3.)
    if payload.pharmacy_id:
        ph = (db.query(LarcPharmacy)
                .filter(LarcPharmacy.id == payload.pharmacy_id).first())
        if not ph:
            raise HTTPException(status_code=404, detail="pharmacy not found")
        if not ph.is_active:
            raise HTTPException(
                status_code=409,
                detail=f"Pharmacy {ph.name!r} is inactive — pick another")
        if not (ph.fax or "").strip():
            raise HTTPException(
                status_code=422,
                detail=f"Pharmacy {ph.name!r} has no fax number on file — "
                       "set one in the pharmacy directory first")
        a.pharmacy_id = payload.pharmacy_id
    elif a.pharmacy_id:
        # Re-validate the existing pharmacy link too — staff may be
        # faxing manually after marking pharmacy_id via assignment patch.
        ph = (db.query(LarcPharmacy)
                .filter(LarcPharmacy.id == a.pharmacy_id).first())
        if ph and (not ph.is_active or not (ph.fax or "").strip()):
            raise HTTPException(
                status_code=409,
                detail=(f"Pharmacy on this assignment is "
                        f"{'inactive' if not ph.is_active else 'missing a fax number'}; "
                        "update the directory or pass a different pharmacy_id"))
    a.request_faxed_at = now_utc_naive()
    # SLA: expect device within PHARMACY_ORDER_SLA_DAYS
    from app.services.larc.workflow import PHARMACY_ORDER_SLA_DAYS
    a.expected_received_by = (now_utc_naive().date() + timedelta(days=PHARMACY_ORDER_SLA_DAYS))
    _mark_milestone(a, "request_faxed", status="done", by=by, notes=payload.notes)
    log_audit(db, actor=by, action="request_faxed",
              device=a.device, assignment=a,
              summary=f"Faxed pharmacy order for {a.patient_name} — expect by {a.expected_received_by}")
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


class ReceiveDeviceIn(BaseModel):
    our_id: str
    manufacturer_lot: Optional[str] = None
    manufacturer_serial: Optional[str] = None
    expiration_date: Optional[str] = None
    location: str = "white_plains"
    purchase_price: Optional[DollarAmount] = None
    device_type_id: Optional[str] = None
    notes: Optional[str] = None


@router.post("/assignments/{assignment_id}/receive-device")
def receive_device(assignment_id: str, payload: ReceiveDeviceIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """The pharmacy-shipped device arrived — mint a LarcDevice row with
    our_id + lot, bind it to this assignment, and mark milestones done.
    Idempotent: re-running with the same our_id rejects."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="receive device")
    if a.source_flow != "pharmacy_order":
        raise HTTPException(status_code=409, detail="Only pharmacy_order assignments")
    if a.device_id:
        raise HTTPException(status_code=409,
                            detail="Assignment already has a device bound")
    if payload.location not in LOCATIONS:
        raise HTTPException(status_code=422, detail=f"location must be one of {LOCATIONS}")

    # Device type — payload wins, else inferred from any prior linked device, else fail
    dt_id = payload.device_type_id
    if not dt_id:
        # Try to find from prior assignment context (e.g., re-orders for same patient)
        raise HTTPException(status_code=422,
                            detail="device_type_id required for pharmacy-order receipt")

    dt = db.query(LarcDeviceType).filter(LarcDeviceType.id == dt_id).first()
    if not dt:
        raise HTTPException(status_code=404, detail="device_type not found")

    existing = db.query(LarcDevice).filter(LarcDevice.our_id == payload.our_id.strip()).first()
    if existing:
        raise HTTPException(status_code=409,
                            detail=f"Device with our_id={payload.our_id} already exists")

    by = current_user.get("email") or "system"
    d = LarcDevice(
        our_id=payload.our_id.strip(),
        device_type_id=dt.id,
        manufacturer_lot=payload.manufacturer_lot,
        manufacturer_serial=payload.manufacturer_serial,
        expiration_date=_parse_date(payload.expiration_date, "expiration_date"),
        purchase_price=payload.purchase_price or dt.typical_cost,
        purchase_date=_date.today(),
        location=payload.location,
        status="assigned",
        notes=payload.notes,
    )
    db.add(d); db.flush()
    a.device_id = d.id
    a.device_received_at = now_utc_naive()
    _mark_milestone(a, "device_received", status="done", by=by, notes=payload.notes)
    log_audit(db, actor=by, action="device_received",
              device=d, assignment=a,
              summary=(f"Received {dt.name} #{d.our_id} (lot {d.manufacturer_lot or '—'}) "
                       f"for {a.patient_name}"),
              detail={"manufacturer_lot": d.manufacturer_lot,
                       "expiration_date": str(d.expiration_date) if d.expiration_date else None})
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


# ─── Checkout request / approval / outcome (Phase 4) ───────────────

class CheckoutRequestIn(BaseModel):
    given_to: Optional[str] = None      # who the MA is handing the device to
    patient_dob: str                     # YYYY-MM-DD — verifies patient identity


@router.post("/assignments/{assignment_id}/checkout-request")
def request_checkout(assignment_id: str, payload: CheckoutRequestIn,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """MA / provider requests to check a device out of the cabinet for
    insertion. Hybrid approval: auto-approved when every gate is green;
    otherwise flagged for manager approval.

    Auto-approval gates:
      - Assignment is active and the patient appointment is for today
      - benefits_verified milestone done
      - Patient DOB matches what the MA entered (identity check)
      - Device is currently 'assigned' (not lost/defective/inserted/billed)
    """
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="request a checkout")
    if not a.device_id:
        raise HTTPException(status_code=409,
                            detail="No device bound to this assignment yet — receive the pharmacy order first")
    device = a.device

    # Identity check
    try:
        dob = datetime.strptime(payload.patient_dob[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="patient_dob must be YYYY-MM-DD")
    identity_ok = (a.patient_dob == dob) if a.patient_dob else False

    # Same-day appt check
    today_local = _date.today()
    appt_today = (a.appt_date == today_local) if a.appt_date else False

    # Benefits done
    benefits_done = any(m.kind == "benefits_verified" and m.status == "done"
                        for m in (a.milestones or []))

    # Device status check
    device_ok = device.status in ("assigned", "unassigned")

    # Pending or duplicate checkout?
    pending = (db.query(LarcCheckout)
                 .filter(LarcCheckout.assignment_id == a.id,
                         LarcCheckout.approval_status == "pending")
                 .first())
    if pending:
        raise HTTPException(status_code=409,
                            detail="There's already a pending checkout for this assignment")

    gates_passed = identity_ok and appt_today and benefits_done and device_ok
    approval_kind = "auto" if gates_passed else "manager"
    approval_status = "approved" if gates_passed else "pending"

    by = current_user.get("email") or "system"
    c = LarcCheckout(
        device_id=device.id,
        assignment_id=a.id,
        requested_by=by,
        approval_kind=approval_kind,
        approval_status=approval_status,
        given_to=payload.given_to,
    )
    if approval_status == "approved":
        c.approved_by = "system:auto-approval"
        c.approved_at = now_utc_naive()
        device.status = "checked_out"
        _mark_milestone(a, "device_checked_out", status="done", by=by)
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        # ix_larc_checkout_inflight_unique caught a parallel staff member
        # who created a competing checkout in the same instant.
        db.rollback()
        raise HTTPException(status_code=409,
            detail="Another staff member just checked out this device "
                   "— refresh and try again")

    failure_reasons = []
    if not identity_ok:    failure_reasons.append("patient DOB mismatch")
    if not appt_today:     failure_reasons.append(f"appt not for today ({a.appt_date or 'not scheduled'})")
    if not benefits_done:  failure_reasons.append("benefits not verified")
    if not device_ok:      failure_reasons.append(f"device status={device.status}")

    log_audit(db, actor=by,
              action=("checkout_auto_approved" if approval_status == "approved"
                      else "checkout_flagged_for_manager"),
              device=device, assignment=a, checkout=c,
              summary=(f"{by.split('@')[0]} {'checked out' if approval_status == 'approved' else 'requested checkout for'} "
                       f"{device.device_type.name if device.device_type else 'device'} #{device.our_id} "
                       f"for {a.patient_name}"),
              detail={"approval_kind": approval_kind, "approval_status": approval_status,
                       "gate_failures": failure_reasons,
                       "given_to": payload.given_to})
    db.commit(); db.refresh(c)
    return {
        "checkout_id": str(c.id),
        "approval_kind": approval_kind,
        "approval_status": approval_status,
        "gate_failures": failure_reasons,
    }


class CheckoutApprovalIn(BaseModel):
    approve: bool
    denial_reason: Optional[str] = None


@router.post("/checkouts/{checkout_id}/decide")
def decide_checkout(checkout_id: str, payload: CheckoutApprovalIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    """Manager approves or denies a flagged checkout request."""
    c = (db.query(LarcCheckout)
           .options(joinedload(LarcCheckout.assignment).joinedload(LarcAssignment.device))
           .filter(LarcCheckout.id == checkout_id).first())
    if not c:
        raise HTTPException(status_code=404, detail="checkout not found")
    if c.approval_status != "pending":
        raise HTTPException(status_code=409, detail=f"Already {c.approval_status}")
    if not payload.approve and not (payload.denial_reason or "").strip():
        raise HTTPException(status_code=422, detail="denial_reason required when denying")

    by = current_user.get("email") or "system"
    c.approved_by = by
    c.approved_at = now_utc_naive()
    a = c.assignment
    if payload.approve:
        # Re-validate at approval time — the manager may be clearing a
        # Friday request on Monday and the device may have since moved
        # to lost / defective / expired / inserted, or the assignment
        # may have been billed/closed. Without this, decide_checkout
        # would force the device back to 'checked_out' and silently
        # rewrite a terminal state. (Fable LARC audit H1.)
        if a is None:
            raise HTTPException(
                status_code=409,
                detail="Checkout has no assignment — refuse to approve")
        if a.status in ("billed", "cancelled"):
            raise HTTPException(
                status_code=409,
                detail=f"Assignment is {a.status} — cannot approve checkout")
        device = a.device
        if device is None:
            raise HTTPException(
                status_code=409,
                detail="Assignment has no bound device — cannot approve")
        if device.status not in ("assigned", "checked_out"):
            raise HTTPException(
                status_code=409,
                detail=(f"Device #{device.our_id} is now {device.status!r} — "
                        f"refuse to approve a stale request. Have the staffer "
                        f"start a fresh checkout if appropriate."))
        # Atomic device status flip — only succeeds when the device was
        # 'assigned' (the normal pre-checkout state). 'checked_out'
        # idempotency is preserved (still rowcount=1 once we widen).
        from sqlalchemy import update as _sql_update
        claimed = db.execute(
            _sql_update(LarcDevice)
              .where(LarcDevice.id == device.id,
                     LarcDevice.status.in_(["assigned", "checked_out"]))
              .values(status="checked_out")
        ).rowcount
        if not claimed:
            db.refresh(device)
            raise HTTPException(
                status_code=409,
                detail=(f"Device status changed to {device.status!r} between "
                        "validation and update — refresh and try again."))

        c.approval_status = "approved"
        _mark_milestone(a, "device_checked_out", status="done", by=by)
        action = "checkout_approved"
        summary = f"Manager approved checkout for {a.patient_name}"
    else:
        c.approval_status = "denied"
        c.denial_reason = payload.denial_reason
        action = "checkout_denied"
        summary = f"Manager DENIED checkout for {a.patient_name}: {payload.denial_reason}"

    log_audit(db, actor=by, action=action,
              device=a.device, assignment=a, checkout=c,
              summary=summary, detail={"denial_reason": payload.denial_reason})
    db.commit(); db.refresh(c)
    return {
        "checkout_id": str(c.id),
        "approval_status": c.approval_status,
    }


@router.post("/checkouts/{checkout_id}/acknowledge")
def acknowledge_checkout(checkout_id: str,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Staff confirms they saw a device checkout happen. Clears it from
    the dashboard's "Unacknowledged checkouts" list."""
    c = (db.query(LarcCheckout)
           .options(joinedload(LarcCheckout.assignment).joinedload(LarcAssignment.device))
           .filter(LarcCheckout.id == checkout_id).first())
    if not c:
        raise HTTPException(status_code=404, detail="checkout not found")
    if c.acknowledged_at:
        return {"checkout_id": str(c.id),
                "acknowledged_at": c.acknowledged_at.isoformat(),
                "acknowledged_by": c.acknowledged_by}

    by = current_user.get("email") or "system"
    c.acknowledged_at = now_utc_naive()
    c.acknowledged_by = by

    a = c.assignment
    log_audit(db, actor=by, action="checkout_acknowledged",
              device=a.device if a else None, assignment=a, checkout=c,
              summary=f"Acknowledged checkout for {a.patient_name if a else '?'}")
    db.commit(); db.refresh(c)
    return {"checkout_id": str(c.id),
            "acknowledged_at": c.acknowledged_at.isoformat(),
            "acknowledged_by": c.acknowledged_by}


@router.get("/checkouts/pending")
def list_pending_checkouts(db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    """Manager queue: every checkout flagged for review."""
    rows = (db.query(LarcCheckout)
              .options(joinedload(LarcCheckout.assignment).joinedload(LarcAssignment.device).joinedload(LarcDevice.device_type))
              .filter(LarcCheckout.approval_status == "pending")
              .order_by(LarcCheckout.requested_at)
              .all())
    return [
        {
            "id": str(c.id),
            "requested_by": c.requested_by,
            "requested_at": c.requested_at.isoformat(),
            "patient_name": c.assignment.patient_name if c.assignment else None,
            "chart_number": c.assignment.chart_number if c.assignment else None,
            "device_our_id": c.assignment.device.our_id if (c.assignment and c.assignment.device) else None,
            "device_type": (c.assignment.device.device_type.name
                            if (c.assignment and c.assignment.device and c.assignment.device.device_type) else None),
            "given_to": c.given_to,
        }
        for c in rows
    ]


@router.get("/checkouts/ready")
def list_ready_to_checkout(db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Assignments whose device is on-hand and ready to be checked out for
    insertion. Returned fields intentionally omit the device's our_id —
    staff must read the physical label and type it back in to confirm."""
    rows = (db.query(LarcAssignment)
              .options(joinedload(LarcAssignment.device).joinedload(LarcDevice.device_type))
              .filter(LarcAssignment.device_id.isnot(None),
                      LarcAssignment.status.notin_(["billed", "cancelled"]),
                      LarcAssignment.is_active == True)
              .all())
    out = []
    for a in rows:
        d = a.device
        if not d or d.status not in ("assigned", "unassigned"):
            continue
        pending = (db.query(LarcCheckout)
                     .filter(LarcCheckout.assignment_id == a.id,
                             LarcCheckout.approval_status == "pending")
                     .first())
        if pending:
            continue
        out.append({
            "assignment_id": str(a.id),
            "patient_name": a.patient_name,
            "chart_number": a.chart_number,
            "appt_date": str(a.appt_date) if a.appt_date else None,
            "device_type_name": d.device_type.name if d.device_type else None,
        })
    out.sort(key=lambda r: (r["appt_date"] or "9999", r["patient_name"] or ""))
    return out


class CheckoutDirectIn(BaseModel):
    device_our_id: str
    given_to: Optional[str] = None


@router.post("/assignments/{assignment_id}/checkout-direct")
def checkout_direct(assignment_id: str, payload: CheckoutDirectIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Staff-initiated checkout that bypasses the 4 standard gates (DOB,
    same-day appt, benefits verified, device status). The confirmation
    safeguard is that the user must physically read the device's our_id
    off the label and type it back in. Mismatches are rejected."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="check out a device")
    if not a.device_id:
        raise HTTPException(status_code=409,
                            detail="No device bound to this assignment yet")
    device = a.device

    entered = (payload.device_our_id or "").strip()
    if not entered:
        raise HTTPException(status_code=422, detail="device_our_id required")
    if entered.lower() != (device.our_id or "").strip().lower():
        raise HTTPException(status_code=422,
                            detail="Device ID does not match the device assigned to this patient")

    pending = (db.query(LarcCheckout)
                 .filter(LarcCheckout.assignment_id == a.id,
                         LarcCheckout.approval_status == "pending")
                 .first())
    if pending:
        raise HTTPException(status_code=409,
                            detail="There's already a pending checkout for this assignment")

    if device.status not in ("assigned", "unassigned"):
        raise HTTPException(status_code=409,
                            detail=f"Device is not available (status={device.status})")

    by = current_user.get("email") or "system"
    c = LarcCheckout(
        device_id=device.id,
        assignment_id=a.id,
        requested_by=by,
        approval_kind="direct",
        approval_status="approved",
        approved_by=by,
        approved_at=now_utc_naive(),
        given_to=payload.given_to,
    )
    device.status = "checked_out"
    _mark_milestone(a, "device_checked_out", status="done", by=by)
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        # ix_larc_checkout_inflight_unique caught a parallel staff member
        # who created a competing checkout in the same instant. Roll back
        # and surface a clean 409 instead of a 500.
        db.rollback()
        raise HTTPException(status_code=409,
            detail="Another staff member just checked out this device "
                   "— refresh and try again")

    log_audit(db, actor=by, action="checkout_direct",
              device=device, assignment=a, checkout=c,
              summary=(f"{by.split('@')[0]} directly checked out "
                       f"{device.device_type.name if device.device_type else 'device'} #{device.our_id} "
                       f"for {a.patient_name}"),
              detail={"approval_kind": "direct", "approval_status": "approved",
                       "given_to": payload.given_to,
                       "bypassed_gates": True})
    db.commit(); db.refresh(c)
    return {
        "checkout_id": str(c.id),
        "approval_kind": "direct",
        "approval_status": "approved",
    }


class BilledIn(BaseModel):
    claim_number: str


@router.post("/assignments/{assignment_id}/bill")
def mark_billed(assignment_id: str, payload: BilledIn,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Record the ModMed claim number. Marks billed milestone done and
    moves the assignment off the active dashboard (findable in history)."""
    a = _load_assignment(db, assignment_id)
    if not payload.claim_number.strip():
        raise HTTPException(status_code=422, detail="claim_number required")
    if a.status != "inserted":
        raise HTTPException(status_code=409,
                            detail=f"Can only bill an inserted assignment (current status: {a.status})")
    # Don't bill insurance for devices the patient already paid for.
    if a.device and (a.device.ownership or "wwc_owned") == "patient_owned":
        raise HTTPException(
            status_code=409,
            detail="This is a Patient-Owned device — WWC does not bill "
                   "insurance for it. Close out the assignment without a "
                   "claim number or change the device's ownership first.")
    by = current_user.get("email") or "system"
    a.claim_number = payload.claim_number.strip()
    a.billed_at = now_utc_naive()
    a.billed_by = by
    a.status = "billed"
    _mark_milestone(a, "billed", status="done", by=by)
    if a.device:
        a.device.status = "billed"
    log_audit(db, actor=by, action="billed",
              device=a.device, assignment=a,
              summary=f"Billed claim #{a.claim_number} for {a.patient_name}",
              detail={"claim_number": a.claim_number})
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


# ─── Printable device label (PDF with QR) ─────────────────────────

@router.get("/devices/{device_id}/label.pdf")
def device_label_pdf(device_id: str,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    from fastapi.responses import Response
    from app.services.larc.label import render_device_label
    d = (db.query(LarcDevice)
           .options(joinedload(LarcDevice.device_type))
           .filter(LarcDevice.id == device_id).first())
    if not d:
        raise HTTPException(status_code=404, detail="device not found")
    # base_url for QR — pull from a configurable setting if available
    pdf_bytes = render_device_label(d)
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="larc_{d.our_id}_label.pdf"'})


# ─── Office-procedure flow (NovaSure, Bensta) ──────────────────────



class OfficeProcedureAssignmentIn(BaseModel):
    device_id: str
    chart_number: str
    patient_name: str
    patient_dob: Optional[str] = None
    primary_insurance: Optional[str] = None
    linked_surgery_id: Optional[str] = None    # foreign-key-style string to a Surgery row
    appt_date: Optional[str] = None
    patient_responsibility: Optional[DollarAmount] = None
    notes: Optional[str] = None


@router.post("/assignments/office-procedure", status_code=201)
def create_office_procedure_assignment(
    payload: OfficeProcedureAssignmentIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK)),
):
    """Create an office-procedure assignment (NovaSure / Bensta etc.). The
    device must be unallocated and of an office_procedure-category type.
    Auto-marks the device_assigned milestone done."""
    device = (db.query(LarcDevice)
                .options(joinedload(LarcDevice.device_type))
                .filter(LarcDevice.id == payload.device_id).first())
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    if device.status != "unassigned":
        raise HTTPException(status_code=409,
                            detail=f"Device #{device.our_id} is not unassigned (status: {device.status})")
    if not device.device_type or device.device_type.category != "office_procedure":
        raise HTTPException(status_code=409,
                            detail=f"Device is not an office_procedure type")
    # 1-active-assignment-per-device enforcement
    active = (db.query(LarcAssignment)
                .filter(LarcAssignment.device_id == device.id,
                        LarcAssignment.is_active.is_(True))
                .first())
    if active:
        raise HTTPException(status_code=409,
                            detail=f"Device already has active assignment to {active.patient_name}")

    by = current_user.get("email") or "system"
    a = LarcAssignment(
        device_id=device.id,
        chart_number=payload.chart_number.strip(),
        patient_name=payload.patient_name.strip(),
        patient_dob=_parse_date(payload.patient_dob, "patient_dob"),
        primary_insurance=payload.primary_insurance,
        linked_surgery_id=payload.linked_surgery_id,
        source_flow="office_procedure",
        status="in_progress",
        is_active=True,
        patient_responsibility=payload.patient_responsibility,
        appt_date=_parse_date(payload.appt_date, "appt_date"),
        notes=payload.notes,
        created_by=by,
    )
    db.add(a); db.flush()
    spawn_milestones(db, a)
    # Mark the device_assigned milestone done (since the assignment IS the picking)
    from app.services.larc.workflow import LarcMilestone as _M  # avoid shadowing
    m = next((mm for mm in a.milestones if mm.kind == "device_assigned"), None)
    if m:
        m.status = "done"
        m.completed_at = now_utc_naive()
        m.completed_by = by
    device.status = "assigned"
    log_audit(db, actor=by, action="op_assignment_created",
              device=device, assignment=a,
              summary=(f"Picked {device.device_type.name} #{device.our_id} for {a.patient_name}"
                       + (f" (surgery {payload.linked_surgery_id})" if payload.linked_surgery_id else "")),
              detail={"linked_surgery_id": payload.linked_surgery_id})
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


class ConsumeIn(BaseModel):
    consumed_at: Optional[str] = None       # YYYY-MM-DD HH:MM:SS, defaults to now
    notes: Optional[str] = None


@router.post("/assignments/{assignment_id}/consume")
def consume_device(assignment_id: str, payload: ConsumeIn = ConsumeIn(),
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Mark an office-procedure device as consumed (used during the
    procedure). Device.status → 'inserted' (we reuse the LARC term so the
    rest of the dashboard works). Next: record claim # via /bill."""
    a = _load_assignment(db, assignment_id)
    _block_if_closed_or_billed(a, action="consume this device")
    if a.source_flow != "office_procedure":
        raise HTTPException(status_code=409,
                            detail="Consume only applies to office_procedure assignments")
    if not a.device_id:
        raise HTTPException(status_code=409, detail="No device on this assignment")
    by = current_user.get("email") or "system"
    a.inserted_at = now_utc_naive()
    a.inserted_by = by
    a.status = "inserted"
    _mark_milestone(a, "device_consumed", status="done", by=by, notes=payload.notes)
    if a.device:
        a.device.status = "inserted"
    log_audit(db, actor=by, action="device_consumed",
              device=a.device, assignment=a,
              summary=f"Consumed device #{a.device.our_id if a.device else ''} for {a.patient_name}",
              detail={"notes": payload.notes})
    db.commit(); db.refresh(a)
    return _assignment_dict(a, include_milestones=True)


# ─── Defective device → manufacturer return chain ──────────────────

class ReturnToManufacturerIn(BaseModel):
    rma_number: Optional[str] = None
    return_method: Optional[str] = None    # 'fedex' | 'ups' | 'usps' | 'manufacturer_pickup'
    tracking_number: Optional[str] = None
    notes: Optional[str] = None


@router.post("/devices/{device_id}/return-to-manufacturer")
def return_to_manufacturer(device_id: str, payload: ReturnToManufacturerIn,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Record that a defective device has been shipped back to the
    manufacturer for replacement. Moves device.status from 'defective'
    to 'returned'."""
    d = db.query(LarcDevice).options(joinedload(LarcDevice.device_type))\
          .filter(LarcDevice.id == device_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="device not found")
    if d.status not in ("defective", "returned"):
        raise HTTPException(status_code=409,
                            detail=f"Device must be 'defective' to return (current: {d.status})")
    by = current_user.get("email") or "system"
    d.status = "returned"
    return_blob = {
        "rma_number": payload.rma_number,
        "return_method": payload.return_method,
        "tracking_number": payload.tracking_number,
        "returned_at": now_utc_naive().isoformat(),
        "returned_by": by,
    }
    d.notes = ((d.notes or "") +
               f"\n\n[returned to manufacturer {now_utc_naive().date()}] " +
               f"RMA={payload.rma_number or '—'}, method={payload.return_method or '—'}, " +
               f"tracking={payload.tracking_number or '—'}" +
               (f", notes: {payload.notes}" if payload.notes else ""))
    log_audit(db, actor=by, action="device_returned_to_mfr",
              device=d,
              summary=(f"Returned defective {d.device_type.name if d.device_type else 'device'} "
                       f"#{d.our_id} to manufacturer"
                       + (f" (RMA {payload.rma_number})" if payload.rma_number else "")),
              detail=return_blob)
    db.commit(); db.refresh(d)
    return _device_dict(d)


class ReceiveReplacementIn(BaseModel):
    """Create a new physical-device row that replaces a defective one.
    The new device auto-binds to the same active assignment (if any)."""
    new_our_id: str
    new_manufacturer_lot: Optional[str] = None
    new_manufacturer_serial: Optional[str] = None
    new_expiration_date: Optional[str] = None
    new_location: str = "white_plains"
    new_purchase_price: Optional[DollarAmount] = None    # often 0 if manufacturer-replaced
    notes: Optional[str] = None


@router.post("/devices/{device_id}/receive-replacement")
def receive_replacement(device_id: str, payload: ReceiveReplacementIn,
                         db: Session = Depends(get_db),
                         current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """The manufacturer-supplied replacement arrived. Mint a new LarcDevice
    row with the bi-directional replacement link, and re-bind any active
    assignment to the new device so the workflow can continue from
    'device received'."""
    original = db.query(LarcDevice).options(joinedload(LarcDevice.device_type))\
                  .filter(LarcDevice.id == device_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="device not found")
    if original.status != "returned":
        raise HTTPException(status_code=409,
                            detail=f"Original device must be 'returned' first (current: {original.status})")
    if payload.new_location not in LOCATIONS:
        raise HTTPException(status_code=422, detail=f"location must be one of {LOCATIONS}")
    existing = db.query(LarcDevice).filter(LarcDevice.our_id == payload.new_our_id.strip()).first()
    if existing:
        raise HTTPException(status_code=409,
                            detail=f"A device with our_id={payload.new_our_id} already exists")

    by = current_user.get("email") or "system"
    new_dev = LarcDevice(
        our_id=payload.new_our_id.strip(),
        device_type_id=original.device_type_id,
        manufacturer_lot=payload.new_manufacturer_lot,
        manufacturer_serial=payload.new_manufacturer_serial,
        expiration_date=_parse_date(payload.new_expiration_date, "new_expiration_date"),
        purchase_date=_date.today(),
        purchase_price=payload.new_purchase_price,
        location=payload.new_location,
        status="unassigned",
        replaces_device_id=original.id,
        notes=payload.notes,
    )
    db.add(new_dev); db.flush()
    original.replacement_device_id = new_dev.id

    # If the original device had an active failed_used assignment, re-bind
    # the new device to it and reset state so the patient can be inserted.
    active = (db.query(LarcAssignment)
                .options(joinedload(LarcAssignment.milestones))
                .filter(LarcAssignment.device_id == original.id,
                        LarcAssignment.is_active.is_(True),
                        LarcAssignment.status == "failed_used")
                .first())
    if active:
        # Mark the OLD assignment as replaced; spawn a NEW assignment on the
        # replacement device so the original failure stays in the audit chain.
        old_assignment_id = active.id
        replacement = LarcAssignment(
            device_id=new_dev.id,
            # Carry over every demographic / identity field needed to
            # regenerate forms. Without the address / name-part columns
            # a re-sent pharmacy enrollment used to arrive at the
            # pharmacy with blank demographics. (Fable LARC audit L5.)
            chart_number=active.chart_number,
            patient_name=active.patient_name,
            patient_first_name=active.patient_first_name,
            patient_middle_initial=active.patient_middle_initial,
            patient_last_name=active.patient_last_name,
            patient_dob=active.patient_dob,
            patient_email=active.patient_email,
            patient_phone=active.patient_phone,
            patient_address=active.patient_address,
            patient_city=active.patient_city,
            patient_state=active.patient_state,
            patient_zip=active.patient_zip,
            primary_insurance=active.primary_insurance,
            pharmacy_id=active.pharmacy_id,
            device_type_id=active.device_type_id,
            source_flow=active.source_flow,
            status="in_progress",
            is_active=True,
            patient_responsibility=active.patient_responsibility,
            replaces_assignment_id=active.id,
            notes=(f"Replacement assignment after defective device {original.our_id} "
                   f"(original assignment {active.id})."),
            created_by=by,
        )
        db.add(replacement); db.flush()
        spawn_milestones(db, replacement)
        # Carry over the milestones that don't need re-doing
        ms_old = {m.kind: m for m in active.milestones}
        ms_new = {m.kind: m for m in replacement.milestones}
        for kind in ("benefits_verified", "enrollment_sent", "enrollment_signed",
                     "patient_responsibility_modmed"):
            o = ms_old.get(kind)
            n = ms_new.get(kind)
            if o and o.status == "done" and n and n.status == "pending":
                n.status = "done"
                n.completed_at = o.completed_at
                n.completed_by = o.completed_by
                n.notes = "Carried over from original assignment."
        # The new device-received milestone is now done
        if (n := ms_new.get("device_received")) and n.status == "pending":
            n.status = "done"
            n.completed_at = now_utc_naive()
            n.completed_by = by
        active.is_active = False
        active.replacement_assignment_id = replacement.id
        new_dev.status = "assigned"
        log_audit(db, actor=by, action="replacement_received",
                  device=new_dev, assignment=replacement,
                  summary=(f"Manufacturer replacement #{new_dev.our_id} arrived for "
                           f"{active.patient_name} — rebound to new assignment"),
                  detail={"original_device_id": str(original.id),
                           "original_assignment_id": str(old_assignment_id)})
    else:
        log_audit(db, actor=by, action="replacement_received",
                  device=new_dev,
                  summary=(f"Manufacturer replacement #{new_dev.our_id} arrived for "
                           f"defective #{original.our_id} — added to stock"),
                  detail={"original_device_id": str(original.id)})

    db.commit(); db.refresh(new_dev)
    return _device_dict(new_dev)


# ─── Sweeps + Owed list (Phase 5) ──────────────────────────────────

@router.post("/admin/run-sweeps")
def run_sweeps(current_user: dict = Depends(requires_super_admin())):
    """Manual trigger for the expiry + stale-assignment + pharmacy-SLA
    sweeps. Primary runner is the larc_sweeps Cloud Run Job (registered
    in app/jobs/run.py). Super-admin only. (Fable note 6.)"""
    from app.services.larc.sweeps import run_all
    return run_all()


@router.get("/owed")
def list_owed(db: Session = Depends(get_db),
               current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
               include_resolved: bool = False):
    q = db.query(LarcOwedPatient)
    if not include_resolved:
        q = q.filter(LarcOwedPatient.resolved_at.is_(None))
    rows = q.order_by(LarcOwedPatient.owed_since.desc()).all()
    today = _date.today()
    return [
        {
            "id": str(o.id),
            "chart_number": o.chart_number,
            "patient_name": o.patient_name,
            "original_assignment_id": str(o.original_assignment_id),
            "original_device_type_id": str(o.original_device_type_id),
            "owed_since": o.owed_since.isoformat(),
            "expires_at": str(o.expires_at) if o.expires_at else None,
            "days_until_expiry": ((o.expires_at - today).days if o.expires_at else None),
            "resolved_at": o.resolved_at.isoformat() if o.resolved_at else None,
            "resolution": o.resolution,
        }
        for o in rows
    ]


class OwedResolveIn(BaseModel):
    resolution: str   # 'reallocated' | 'declined' | 'expired'
    new_assignment_id: Optional[str] = None
    notes: Optional[str] = None


@router.post("/owed/{owed_id}/resolve")
def resolve_owed(owed_id: str, payload: OwedResolveIn,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Mark an Owed patient as resolved. When 'reallocated', the caller
    has already created a new assignment for the patient (via the
    standard /assignments POST flow); pass that ID for the audit link."""
    o = db.query(LarcOwedPatient).filter(LarcOwedPatient.id == owed_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="owed entry not found")
    if o.resolved_at:
        raise HTTPException(status_code=409, detail="already resolved")
    if payload.resolution not in ("reallocated", "declined", "expired"):
        raise HTTPException(status_code=422, detail="invalid resolution")
    by = current_user.get("email") or "system"
    o.resolved_at = now_utc_naive()
    o.resolved_by = by
    o.resolution = payload.resolution
    o.notes = (o.notes or "") + (f"\n{payload.notes}" if payload.notes else "")
    log_audit(db, actor=by, action="owed_resolved",
              assignment=None,
              detail={"owed_id": str(o.id), "resolution": payload.resolution,
                       "new_assignment_id": payload.new_assignment_id},
              summary=f"Owed list: {o.patient_name} → {payload.resolution}")
    db.commit()
    return {"ok": True, "owed_id": str(o.id), "resolution": payload.resolution}


# ─── Physical inventory count ──────────────────────────────────────

class InventoryCountStartIn(BaseModel):
    scope_location: Optional[str] = None    # one of LOCATIONS, or None = all


@router.post("/inventory-counts/start", status_code=201)
def start_inventory_count(payload: InventoryCountStartIn,
                           db: Session = Depends(get_db),
                           current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    """Begin a physical inventory count session. Snapshots the current
    expected on-hand device set; staff then scans devices and reconciles."""
    if payload.scope_location and payload.scope_location not in LOCATIONS:
        raise HTTPException(status_code=422, detail=f"location must be one of {LOCATIONS}")
    # Bail if there's already an in-progress count
    open_count = (db.query(LarcInventoryCount)
                    .filter(LarcInventoryCount.status == "in_progress")
                    .first())
    if open_count:
        raise HTTPException(status_code=409,
                            detail=f"Inventory count already in progress (started {open_count.started_at})")

    # Snapshot expected devices — anything in cabinet ought to be here
    q = (db.query(LarcDevice)
           .filter(LarcDevice.status.in_(["unassigned", "assigned", "received", "defective"])))
    if payload.scope_location:
        q = q.filter(LarcDevice.location == payload.scope_location)
    expected_ids = [str(d.id) for d in q.all()]

    by = current_user.get("email") or "system"
    count = LarcInventoryCount(
        started_by=by,
        scope_location=payload.scope_location,
        expected_device_ids=expected_ids,
        scanned_device_ids=[],
        status="in_progress",
    )
    db.add(count); db.flush()
    log_audit(db, actor=by, action="inventory_count_started",
              summary=(f"Started physical inventory count "
                       f"({payload.scope_location or 'all locations'}) — "
                       f"{len(expected_ids)} devices expected"))
    db.commit(); db.refresh(count)
    return {"id": str(count.id), "expected_count": len(expected_ids)}


@router.get("/inventory-counts/{count_id}")
def get_inventory_count(count_id: str,
                         db: Session = Depends(get_db),
                         current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    c = db.query(LarcInventoryCount).filter(LarcInventoryCount.id == count_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="count not found")

    expected = set(c.expected_device_ids or [])
    scanned = set(c.scanned_device_ids or [])
    missing_ids = list(expected - scanned)        # expected but not scanned
    unexpected_ids = list(scanned - expected)     # scanned but not expected

    def hydrate(ids):
        if not ids:
            return []
        rows = (db.query(LarcDevice)
                  .options(joinedload(LarcDevice.device_type))
                  .filter(LarcDevice.id.in_(ids)).all())
        return [
            {
                "id": str(d.id), "our_id": d.our_id,
                "device_type_name": d.device_type.name if d.device_type else None,
                "lot": d.manufacturer_lot, "location": d.location,
                "status": d.status,
            }
            for d in rows
        ]

    return {
        "id": str(c.id),
        "status": c.status,
        "scope_location": c.scope_location,
        "started_at": c.started_at.isoformat(),
        "started_by": c.started_by,
        "finished_at": c.finished_at.isoformat() if c.finished_at else None,
        "finished_by": c.finished_by,
        "expected_count": len(expected),
        "scanned_count": len(scanned),
        "missing": hydrate(missing_ids),
        "unexpected": hydrate(unexpected_ids),
        "notes": c.notes,
    }


class InventoryScanIn(BaseModel):
    our_id: str    # the device label scanned (from QR or typed)


@router.post("/inventory-counts/{count_id}/scan")
def scan_for_count(count_id: str, payload: InventoryScanIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Mark a device as scanned during an in-progress count. Idempotent.
    Accepts:
      - bare our_id (e.g. 'WWC0700')
      - full QR-coded URL ('http://.../larc/devices/<id>') — extracts the id
      - bare device UUID
    """
    # Lock the count row for the duration of this transaction so two
    # concurrent scanners can't both read scanned_device_ids, both
    # append, and let the later commit overwrite the first. Lost scans
    # become false "missing" devices at finish_count → false 'lost'
    # markings. (Fable LARC audit M9.)
    c = (db.query(LarcInventoryCount)
            .filter(LarcInventoryCount.id == count_id)
            .with_for_update()
            .first())
    if not c:
        raise HTTPException(status_code=404, detail="count not found")
    if c.status != "in_progress":
        raise HTTPException(status_code=409, detail=f"Count is {c.status}")

    raw = payload.our_id.strip()
    d = None
    # If it looks like a URL, pull the trailing UUID
    if "/larc/devices/" in raw:
        device_uuid = raw.rsplit("/larc/devices/", 1)[-1].strip("/").split("?")[0]
        d = db.query(LarcDevice).filter(LarcDevice.id == device_uuid).first()
    # Otherwise try our_id, then bare UUID
    if not d:
        d = db.query(LarcDevice).filter(LarcDevice.our_id == raw).first()
    if not d:
        d = db.query(LarcDevice).filter(LarcDevice.id == raw).first()
    if not d:
        raise HTTPException(status_code=404,
                            detail=f"No device matches {raw!r}")
    scanned = list(c.scanned_device_ids or [])
    if str(d.id) not in scanned:
        scanned.append(str(d.id))
        c.scanned_device_ids = scanned
        # Tell SQLAlchemy the JSON column changed — mutating a list
        # in-place doesn't trigger the dirty flag on most JSON column
        # types. The reassignment above usually does, but be explicit
        # under the lock so the UPDATE definitely fires.
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(c, "scanned_device_ids")
    db.commit()
    return {"scanned_count": len(scanned), "device_our_id": d.our_id}


class InventoryFinishIn(BaseModel):
    notes: Optional[str] = None


@router.post("/inventory-counts/{count_id}/finish")
def finish_count(count_id: str, payload: InventoryFinishIn = InventoryFinishIn(),
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    """Close out a count. Marks any expected-but-not-scanned devices as
    'lost' (with the count_id in their notes for the audit trail) and
    writes a summary audit event."""
    c = db.query(LarcInventoryCount).filter(LarcInventoryCount.id == count_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="count not found")
    if c.status != "in_progress":
        raise HTTPException(status_code=409, detail=f"Count is {c.status}")
    by = current_user.get("email") or "system"
    expected = set(c.expected_device_ids or [])
    scanned = set(c.scanned_device_ids or [])
    missing = list(expected - scanned)

    # Mark missing as 'lost' so the loss tracking captures them — but
    # skip devices whose status has moved off 'unassigned'/'received'
    # since the count started. A device checked out, inserted, returned,
    # or already-flagged-lost during the count window is "missing" from
    # the cabinet for legitimate reasons; flipping it to 'lost' would
    # silently overwrite the real state and break the billing path for
    # that patient. (Fable LARC audit M4.)
    actually_lost = 0
    skipped_in_flight = []
    if missing:
        rows = (db.query(LarcDevice)
                  .options(joinedload(LarcDevice.device_type))
                  .filter(LarcDevice.id.in_(missing)).all())
        for d in rows:
            if d.status not in ("unassigned", "received"):
                # Device moved during the count window — record on the
                # audit trail but don't overwrite its real status.
                skipped_in_flight.append(
                    {"our_id": d.our_id, "status": d.status})
                log_audit(db, actor=by, action="count_skip_in_flight",
                          device=d,
                          summary=(f"Device {d.our_id} skipped at count finish — "
                                   f"status moved to {d.status!r} during the count "
                                   "window; not flipping to lost"),
                          detail={"count_id": str(c.id), "status": d.status})
                continue
            d.status = "lost"
            d.notes = ((d.notes or "")
                       + f"\n[lost in inventory count {c.id} on {now_utc_naive().date()}]")
            log_audit(db, actor=by, action="device_lost_at_count",
                      device=d,
                      summary=(f"Device {d.our_id} missing at inventory count — marked lost "
                               f"(${d.purchase_price or 0:.2f})"),
                      detail={"count_id": str(c.id)})
            actually_lost += 1

    c.status = "reconciled"
    c.finished_at = now_utc_naive()
    c.finished_by = by
    c.notes = payload.notes
    log_audit(db, actor=by, action="inventory_count_finished",
              summary=(f"Finished inventory count — {len(scanned)}/{len(expected)} scanned, "
                       f"{actually_lost} marked lost, "
                       f"{len(skipped_in_flight)} skipped (in-flight)"),
              detail={"count_id": str(c.id), "scanned": len(scanned),
                       "expected": len(expected),
                       "lost_count": actually_lost,
                       "skipped_in_flight": skipped_in_flight})
    db.commit()
    return {
        "id": str(c.id), "status": "reconciled",
        "lost_count": actually_lost,
        "skipped_in_flight": skipped_in_flight,
    }


@router.get("/inventory-counts")
def list_inventory_counts(db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    rows = (db.query(LarcInventoryCount)
              .order_by(LarcInventoryCount.started_at.desc())
              .limit(20).all())
    return [
        {
            "id": str(c.id),
            "started_at": c.started_at.isoformat(),
            "started_by": c.started_by,
            "finished_at": c.finished_at.isoformat() if c.finished_at else None,
            "scope_location": c.scope_location,
            "status": c.status,
            "expected_count": len(c.expected_device_ids or []),
            "scanned_count": len(c.scanned_device_ids or []),
        }
        for c in rows
    ]


# ─── End-of-day reconciliation report ──────────────────────────────

@router.get("/reports/eod")
def end_of_day_report(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
    date: Optional[str] = None,   # YYYY-MM-DD, defaults to today
):
    """Daily reconciliation — what got checked out, inserted, returned,
    or marked lost today. Match this against the physical cabinet at EOD."""
    target = _parse_date(date, "date") if date else _date.today()
    start_dt = datetime.combine(target, datetime.min.time())
    end_dt = datetime.combine(target, datetime.max.time())
    # PHI export audit (Fable LARC audit M6) — the report renders patient
    # names + chart numbers for every checkout/insertion of the day.
    log_action(
        db,
        action="LARC_EOD_REPORT_VIEWED",
        resource_type="larc_eod_report",
        resource_id=str(target),
        patient_id=None,
        user_id=(current_user.get("email") or "").lower() or None,
        user_name=current_user.get("name") or current_user.get("email"),
        description=f"Viewed LARC end-of-day reconciliation report for {target}",
    )

    # Checkouts requested today (regardless of approval status)
    checkouts_today = (db.query(LarcCheckout)
                         .options(joinedload(LarcCheckout.assignment).joinedload(LarcAssignment.device).joinedload(LarcDevice.device_type))
                         .filter(LarcCheckout.requested_at >= start_dt,
                                 LarcCheckout.requested_at <= end_dt)
                         .order_by(LarcCheckout.requested_at).all())

    # Assignments where insertion was recorded today
    inserted_today = (db.query(LarcAssignment)
                        .options(joinedload(LarcAssignment.device).joinedload(LarcDevice.device_type))
                        .filter(LarcAssignment.inserted_at >= start_dt,
                                LarcAssignment.inserted_at <= end_dt)
                        .all())

    # Audit events for outcome / loss / return today
    outcome_events = (db.query(LarcAuditEvent)
                        .filter(LarcAuditEvent.occurred_at >= start_dt,
                                LarcAuditEvent.occurred_at <= end_dt,
                                LarcAuditEvent.action.in_([
                                    "outcome_recorded", "device_returned_to_mfr",
                                    "replacement_received",
                                ]))
                        .order_by(LarcAuditEvent.occurred_at).all())

    # Loss tally (devices lost or written off)
    lost_devices = (db.query(LarcDevice)
                      .filter(LarcDevice.status == "lost",
                              LarcDevice.updated_at >= start_dt,
                              LarcDevice.updated_at <= end_dt)
                      .all())
    loss_total = sum(float(d.purchase_price or 0) for d in lost_devices)

    return {
        "date": str(target),
        "checkouts": [
            {
                "checkout_id": str(c.id),
                "requested_at": c.requested_at.isoformat(),
                "requested_by": c.requested_by,
                "approval_status": c.approval_status,
                "outcome": c.outcome,
                "device_our_id": (c.assignment.device.our_id
                                   if c.assignment and c.assignment.device else None),
                "device_type": (c.assignment.device.device_type.name
                                if (c.assignment and c.assignment.device
                                    and c.assignment.device.device_type) else None),
                "patient_name": c.assignment.patient_name if c.assignment else None,
                "given_to": c.given_to,
            }
            for c in checkouts_today
        ],
        "inserted": [
            {
                "assignment_id": str(a.id),
                "patient_name": a.patient_name,
                "device_our_id": a.device.our_id if a.device else None,
                "device_type": (a.device.device_type.name
                                if a.device and a.device.device_type else None),
                "inserted_at": a.inserted_at.isoformat(),
                "inserted_by": a.inserted_by,
            }
            for a in inserted_today
        ],
        "outcome_events": [
            {
                "occurred_at": e.occurred_at.isoformat(),
                "actor": e.actor,
                "action": e.action,
                "summary": e.summary,
            }
            for e in outcome_events
        ],
        "lost_devices": [
            {
                "device_id": str(d.id),
                "our_id": d.our_id,
                "device_type": d.device_type.name if d.device_type else None,
                "loss_value": str(d.purchase_price) if d.purchase_price else None,
            }
            for d in lost_devices
        ],
        "loss_total": loss_total,
        "summary": {
            "checkouts_total": len(checkouts_today),
            "checkouts_approved": sum(1 for c in checkouts_today if c.approval_status == "approved"),
            "checkouts_denied": sum(1 for c in checkouts_today if c.approval_status == "denied"),
            "checkouts_pending": sum(1 for c in checkouts_today if c.approval_status == "pending"),
            "inserted_total": len(inserted_today),
            "lost_total": len(lost_devices),
            "outcome_events_total": len(outcome_events),
        },
    }


# ─── Audit log query ────────────────────────────────────────────────

# ─── Editable manual / operating procedures ────────────────────────

@router.get("/manual")
def list_manual_sections(db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW))):
    rows = (db.query(LarcManualSection)
              .order_by(LarcManualSection.sort_order, LarcManualSection.title).all())
    return [
        {
            "id": str(s.id), "slug": s.slug, "title": s.title,
            "body_md": s.body_md, "sort_order": s.sort_order,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "updated_by": s.updated_by,
        }
        for s in rows
    ]


class ManualSectionIn(BaseModel):
    slug: str
    title: str
    body_md: str = ""
    sort_order: int = 0


@router.post("/manual", status_code=201)
def create_manual_section(payload: ManualSectionIn,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    slug = payload.slug.strip().lower().replace(" ", "-")
    if not slug or not payload.title.strip():
        raise HTTPException(status_code=422, detail="slug and title are required")
    existing = db.query(LarcManualSection).filter(LarcManualSection.slug == slug).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Section '{slug}' already exists")
    by = current_user.get("email") or "system"
    row = LarcManualSection(
        slug=slug, title=payload.title.strip(),
        body_md=payload.body_md, sort_order=payload.sort_order,
        updated_by=by,
    )
    db.add(row); db.commit(); db.refresh(row)
    return {"id": str(row.id), "slug": row.slug}


class ManualSectionPatch(BaseModel):
    title: Optional[str] = None
    body_md: Optional[str] = None
    sort_order: Optional[int] = None


@router.patch("/manual/{section_id}")
def patch_manual_section(section_id: str, payload: ManualSectionPatch,
                          db: Session = Depends(get_db),
                          current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    s = db.query(LarcManualSection).filter(LarcManualSection.id == section_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="section not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(s, k, v)
    s.updated_by = current_user.get("email") or "system"
    db.commit(); db.refresh(s)
    return {"id": str(s.id)}


@router.delete("/manual/{section_id}", status_code=204)
def delete_manual_section(section_id: str,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(requires_tier(Module.LARC, Tier.MANAGE))):
    s = db.query(LarcManualSection).filter(LarcManualSection.id == section_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="section not found")
    log_audit(
        db,
        actor=current_user.get("email") or "system",
        action="manual_section_deleted",
        summary=f"Deleted LARC operating-procedure section: {s.title!r}",
        detail={
            "section_id":  str(s.id),
            "title":       s.title,
            "body_excerpt": (s.body_md or "")[:240],
            "sort_order":  s.sort_order,
        },
    )
    db.delete(s); db.commit()
    return None


@router.get("/audit")
def list_audit(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.LARC, Tier.VIEW)),
    actor: Optional[str] = None,
    device_id: Optional[str] = None,
    chart_number: Optional[str] = None,
    action: Optional[str] = None,
    system_only: bool = False,
    page: int = 1,
    per_page: int = 100,
):
    """Filterable audit log. Combine filters with AND; an empty filter
    set returns the most recent events first."""
    q = db.query(LarcAuditEvent)
    if actor:
        q = q.filter(LarcAuditEvent.actor.ilike(f"%{actor}%"))
    if device_id:
        q = q.filter(LarcAuditEvent.device_id == device_id)
    if chart_number:
        q = q.filter(LarcAuditEvent.chart_number == chart_number)
    if action:
        q = q.filter(LarcAuditEvent.action == action)
    if system_only:
        q = q.filter(LarcAuditEvent.actor.ilike("system:%"))
    rows = (q.order_by(LarcAuditEvent.occurred_at.desc())
              .offset((page - 1) * per_page).limit(per_page).all())
    total = q.count()
    return {
        "total": total, "page": page, "per_page": per_page,
        "events": [
            {
                "id": str(e.id),
                "occurred_at": e.occurred_at.isoformat(),
                "actor": e.actor,
                "action": e.action,
                "device_id": str(e.device_id) if e.device_id else None,
                "assignment_id": str(e.assignment_id) if e.assignment_id else None,
                "checkout_id": str(e.checkout_id) if e.checkout_id else None,
                "chart_number": e.chart_number,
                "patient_name": e.patient_name,
                "summary": e.summary,
                "detail": e.detail or {},
            }
            for e in rows
        ],
    }
