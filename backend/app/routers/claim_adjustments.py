"""Claim-level adjustment CRUD (CARC-coded breakdown rows)."""
from decimal import Decimal, InvalidOperation
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.claim import Claim, ClaimAdjustment
from app.services.audit_service import log_action
from app.routers.auth import get_current_user

router = APIRouter(tags=["claim-adjustments"])

EDITABLE_ADJ_FIELDS = {
    "group_code", "reason_code", "amount", "quantity", "reason_description",
}
ADJ_NUMERIC_FIELDS = {"amount", "quantity"}


def _coerce_adj_value(k: str, v):
    if v is None:
        return None
    if k in ADJ_NUMERIC_FIELDS:
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


def _serialize(a: ClaimAdjustment) -> dict:
    return {
        "id": str(a.id),
        "claim_id": str(a.claim_id) if a.claim_id else None,
        "group_code": a.group_code,
        "reason_code": a.reason_code,
        "amount": float(a.amount or 0),
        "quantity": float(a.quantity) if a.quantity is not None else None,
        "reason_description": a.reason_description,
    }


def _patient_id_for_adj(db: Session, claim_adjustment: Optional[ClaimAdjustment]) -> Optional[str]:
    if claim_adjustment is None or claim_adjustment.claim_id is None:
        return None
    claim = db.query(Claim).filter(Claim.id == claim_adjustment.claim_id).first()
    if claim is None or claim.patient_id is None:
        return None
    return str(claim.patient_id)


@router.post("/claims/{claim_id}/adjustments", status_code=201)
def create_claim_adjustment(
    claim_id: str,
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    adj = ClaimAdjustment(claim_id=claim.id)
    new = {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_ADJ_FIELDS:
            continue
        v = _coerce_adj_value(k, raw)
        setattr(adj, k, v)
        new[k] = _audit_val(v)

    db.add(adj)
    db.commit()
    db.refresh(adj)
    log_action(db, "CREATE", "claim_adjustment",
               resource_id=str(adj.id),
               patient_id=str(claim.patient_id) if claim.patient_id else None,
               user_name=current_user.get("email"),
               new_values=new)
    return _serialize(adj)


@router.patch("/claim-adjustments/{adj_id}")
def update_claim_adjustment(
    adj_id: str,
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    adj = db.query(ClaimAdjustment).filter(ClaimAdjustment.id == adj_id).first()
    if not adj:
        raise HTTPException(status_code=404, detail="Claim adjustment not found")

    old, new = {}, {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_ADJ_FIELDS:
            continue
        v = _coerce_adj_value(k, raw)
        cur = getattr(adj, k)
        if cur != v:
            old[k] = _audit_val(cur)
            new[k] = _audit_val(v)
            setattr(adj, k, v)

    patient_id = _patient_id_for_adj(db, adj)
    db.commit()
    if old or new:
        log_action(db, "UPDATE", "claim_adjustment",
                   resource_id=adj_id,
                   patient_id=patient_id,
                   user_name=current_user.get("email"),
                   old_values=old, new_values=new)
    db.refresh(adj)
    return _serialize(adj)


@router.delete("/claim-adjustments/{adj_id}")
def delete_claim_adjustment(
    adj_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    adj = db.query(ClaimAdjustment).filter(ClaimAdjustment.id == adj_id).first()
    if not adj:
        raise HTTPException(status_code=404, detail="Claim adjustment not found")
    patient_id = _patient_id_for_adj(db, adj)
    db.delete(adj)
    db.commit()
    log_action(db, "DELETE", "claim_adjustment",
               resource_id=adj_id,
               patient_id=patient_id,
               user_name=current_user.get("email"))
    return {"ok": True}
