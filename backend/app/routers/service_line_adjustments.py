"""Service-line-level adjustment CRUD (CARC-coded breakdown rows per SL)."""
from decimal import Decimal, InvalidOperation
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.claim import Claim, ServiceLine, ServiceLineAdjustment
from app.services.audit_service import log_action
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier

router = APIRouter(tags=["service-line-adjustments"])

EDITABLE_SLA_FIELDS = {
    "group_code", "reason_code", "amount", "quantity", "reason_description",
}
SLA_NUMERIC_FIELDS = {"amount", "quantity"}


def _coerce_sla_value(k: str, v):
    if v is None:
        return None
    if k in SLA_NUMERIC_FIELDS:
        try:
            return Decimal(str(v))
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=422,
                                detail=f"invalid number for {k}: {v!r}")
    return v


def _audit_val(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


def _serialize(a: ServiceLineAdjustment) -> dict:
    return {
        "id": str(a.id),
        "service_line_id": str(a.service_line_id) if a.service_line_id else None,
        "group_code": a.group_code,
        "reason_code": a.reason_code,
        "amount": float(a.amount or 0),
        "quantity": float(a.quantity) if a.quantity is not None else None,
        "reason_description": a.reason_description,
    }


def _patient_id_for_sla(db: Session, sla: Optional[ServiceLineAdjustment]) -> Optional[str]:
    if sla is None or sla.service_line_id is None:
        return None
    sl = db.query(ServiceLine).filter(ServiceLine.id == sla.service_line_id).first()
    if sl is None or sl.claim_id is None:
        return None
    claim = db.query(Claim).filter(Claim.id == sl.claim_id).first()
    if claim is None or claim.patient_id is None:
        return None
    return str(claim.patient_id)


@router.post("/service-lines/{line_id}/adjustments", status_code=201)
def create_sl_adjustment(
    line_id: str,
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    sl = db.query(ServiceLine).filter(ServiceLine.id == line_id).first()
    if not sl:
        raise HTTPException(status_code=404, detail="Service line not found")

    adj = ServiceLineAdjustment(service_line_id=sl.id)
    new = {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_SLA_FIELDS:
            continue
        v = _coerce_sla_value(k, raw)
        setattr(adj, k, v)
        new[k] = _audit_val(v)

    db.add(adj)
    db.commit()
    db.refresh(adj)

    claim = db.query(Claim).filter(Claim.id == sl.claim_id).first()
    patient_id = str(claim.patient_id) if (claim and claim.patient_id) else None

    log_action(db, "CREATE", "service_line_adjustment",
               resource_id=str(adj.id),
               patient_id=patient_id,
               user_name=current_user.get("email"),
               new_values=new)
    return _serialize(adj)


@router.patch("/service-line-adjustments/{adj_id}")
def update_sl_adjustment(
    adj_id: str,
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    adj = db.query(ServiceLineAdjustment).filter(
        ServiceLineAdjustment.id == adj_id).first()
    if not adj:
        raise HTTPException(status_code=404, detail="Service line adjustment not found")

    old, new = {}, {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_SLA_FIELDS:
            continue
        v = _coerce_sla_value(k, raw)
        cur = getattr(adj, k)
        if cur != v:
            old[k] = _audit_val(cur)
            new[k] = _audit_val(v)
            setattr(adj, k, v)

    patient_id = _patient_id_for_sla(db, adj)
    db.commit()
    if old or new:
        log_action(db, "UPDATE", "service_line_adjustment",
                   resource_id=adj_id,
                   patient_id=patient_id,
                   user_name=current_user.get("email"),
                   old_values=old, new_values=new)
    db.refresh(adj)
    return _serialize(adj)


@router.delete("/service-line-adjustments/{adj_id}")
def delete_sl_adjustment(
    adj_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    adj = db.query(ServiceLineAdjustment).filter(
        ServiceLineAdjustment.id == adj_id).first()
    if not adj:
        raise HTTPException(status_code=404, detail="Service line adjustment not found")
    patient_id = _patient_id_for_sla(db, adj)
    db.delete(adj)
    db.commit()
    log_action(db, "DELETE", "service_line_adjustment",
               resource_id=adj_id,
               patient_id=patient_id,
               user_name=current_user.get("email"))
    return {"ok": True}
