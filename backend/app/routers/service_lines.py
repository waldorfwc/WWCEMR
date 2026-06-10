"""Service-line CRUD — nested under claims for POST, flat for PATCH/DELETE."""
from datetime import date as date_cls
from decimal import Decimal, InvalidOperation
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.claim import Claim, ServiceLine
from app.services.audit_service import log_action
from app.services.claim_math import recompute_balance
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier

router = APIRouter(tags=["service-lines"])

EDITABLE_SL_FIELDS = {
    "procedure_code", "modifier_1", "modifier_2", "modifier_3", "modifier_4",
    "revenue_code", "units", "description",
    "date_of_service_from", "date_of_service_to",
    "billed_amount", "allowed_amount", "paid_amount",
    "patient_responsibility", "contractual_adjustment", "other_adjustment",
    "diagnosis_codes",
}

SL_MONEY_FIELDS = {
    "billed_amount", "allowed_amount", "paid_amount",
    "patient_responsibility", "contractual_adjustment", "other_adjustment",
}

SL_DATE_FIELDS = {"date_of_service_from", "date_of_service_to"}

SL_NUMERIC_FIELDS = SL_MONEY_FIELDS | {"units"}


def _coerce_sl_value(k: str, v):
    if v is None:
        return None
    if k in SL_NUMERIC_FIELDS:
        try:
            d = Decimal(str(v))
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"invalid number for {k}: {v!r}")
        if not d.is_finite():
            raise HTTPException(status_code=422,
                                detail=f"{k} must be a finite number, got {v!r}")
        return d
    if k in SL_DATE_FIELDS:
        if isinstance(v, str):
            try:
                return date_cls.fromisoformat(v)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"invalid date for {k}: {v!r}")
    if k == "diagnosis_codes":
        if not isinstance(v, list):
            raise HTTPException(status_code=422, detail="diagnosis_codes must be a list")
    return v


def _audit_val(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date_cls):
        return v.isoformat()
    return v


def _serialize(sl: ServiceLine) -> dict:
    return {
        "id": str(sl.id),
        "claim_id": str(sl.claim_id) if sl.claim_id else None,
        "procedure_code": sl.procedure_code,
        "modifier_1": sl.modifier_1,
        "modifier_2": sl.modifier_2,
        "modifier_3": sl.modifier_3,
        "modifier_4": sl.modifier_4,
        "revenue_code": sl.revenue_code,
        "units": float(sl.units) if sl.units is not None else None,
        "description": sl.description,
        "date_of_service_from": sl.date_of_service_from.isoformat() if sl.date_of_service_from else None,
        "date_of_service_to": sl.date_of_service_to.isoformat() if sl.date_of_service_to else None,
        "billed_amount": float(sl.billed_amount or 0),
        "allowed_amount": float(sl.allowed_amount or 0),
        "paid_amount": float(sl.paid_amount or 0),
        "patient_responsibility": float(sl.patient_responsibility or 0),
        "contractual_adjustment": float(sl.contractual_adjustment or 0),
        "other_adjustment": float(sl.other_adjustment or 0),
        "diagnosis_codes": sl.diagnosis_codes or [],
    }


def _patient_id_for_claim(claim: Optional[Claim]) -> Optional[str]:
    if claim is None or claim.patient_id is None:
        return None
    return str(claim.patient_id)


@router.post("/claims/{claim_id}/service-lines", status_code=201)
def create_service_line(
    claim_id: str,
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    sl = ServiceLine(claim_id=claim.id)
    new = {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_SL_FIELDS:
            continue
        v = _coerce_sl_value(k, raw)
        setattr(sl, k, v)
        new[k] = _audit_val(v)

    db.add(sl)
    recompute_balance(claim)
    db.commit()
    db.refresh(sl)
    log_action(db, "CREATE", "service_line",
               resource_id=str(sl.id),
               patient_id=_patient_id_for_claim(claim),
               user_name=current_user.get("email"),
               new_values=new)
    return _serialize(sl)


@router.patch("/service-lines/{line_id}")
def update_service_line(
    line_id: str,
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    sl = db.query(ServiceLine).filter(ServiceLine.id == line_id).first()
    if not sl:
        raise HTTPException(status_code=404, detail="Service line not found")

    old, new = {}, {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_SL_FIELDS:
            continue
        v = _coerce_sl_value(k, raw)
        cur = getattr(sl, k)
        if cur != v:
            old[k] = _audit_val(cur)
            new[k] = _audit_val(v)
            setattr(sl, k, v)

    claim = db.query(Claim).filter(Claim.id == sl.claim_id).first()
    if claim is not None:
        recompute_balance(claim)

    db.commit()
    if old or new:
        log_action(db, "UPDATE", "service_line",
                   resource_id=line_id,
                   patient_id=_patient_id_for_claim(claim),
                   user_name=current_user.get("email"),
                   old_values=old, new_values=new)
    db.refresh(sl)
    return _serialize(sl)


@router.delete("/service-lines/{line_id}")
def delete_service_line(
    line_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    sl = db.query(ServiceLine).filter(ServiceLine.id == line_id).first()
    if not sl:
        raise HTTPException(status_code=404, detail="Service line not found")

    claim_id = sl.claim_id
    db.delete(sl)
    db.flush()

    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if claim is not None:
        recompute_balance(claim)

    db.commit()
    log_action(db, "DELETE", "service_line",
               resource_id=line_id,
               patient_id=_patient_id_for_claim(claim),
               user_name=current_user.get("email"))
    return {"ok": True}
