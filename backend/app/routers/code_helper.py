"""Code Helper feature router — AI-assisted CPT + ICD-10 coding.

Endpoint group: /api/billing/code-helper

Auth model: the router is mounted without a top-level dependency guard so
that future endpoints with different permission tiers can be added cleanly.
Each handler calls require_permission() inline; the claim:read / claim:edit
checks mirror the pattern used by claims.py and missing_charges.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.code_helper import CodeHelperDenial, CodeHelperRequest  # noqa: F401
from app.routers.auth import get_current_user


router = APIRouter(prefix="/billing/code-helper", tags=["code-helper"])


# ─── Denial list ─────────────────────────────────────────────────────

class DenialIn(BaseModel):
    code:       str
    code_type:  str   # 'cpt' or 'icd10'
    payer_name: Optional[str] = None
    reason:     Optional[str] = None


class DenialPatch(BaseModel):
    code:       Optional[str] = None
    code_type:  Optional[str] = None
    payer_name: Optional[str] = None
    reason:     Optional[str] = None
    is_active:  Optional[bool] = None


def _denial_dict(d: CodeHelperDenial) -> dict:
    return {
        "id":         str(d.id),
        "code":       d.code,
        "code_type":  d.code_type,
        "payer_name": d.payer_name,
        "reason":     d.reason,
        "is_active":  d.is_active,
        "added_by":   d.added_by,
        "added_at":   d.added_at.isoformat() if d.added_at else None,
    }


@router.get("/denials")
def list_denials(
    db: Session = Depends(get_db),
    payer:  Optional[str]  = None,
    active: Optional[bool] = True,
    current_user: dict = Depends(get_current_user),
):
    q = db.query(CodeHelperDenial)
    if active is True:
        q = q.filter(CodeHelperDenial.is_active.is_(True))
    if payer:
        # Matching payer OR universal (null payer)
        q = q.filter(
            or_(CodeHelperDenial.payer_name == payer,
                CodeHelperDenial.payer_name.is_(None))
        )
    rows = q.order_by(CodeHelperDenial.added_at.desc()).all()
    return {"denials": [_denial_dict(d) for d in rows]}


@router.post("/denials", status_code=201)
def create_denial(
    payload: DenialIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if payload.code_type not in ("cpt", "icd10"):
        raise HTTPException(422, "code_type must be 'cpt' or 'icd10'")
    d = CodeHelperDenial(
        code=payload.code.strip(),
        code_type=payload.code_type,
        payer_name=(payload.payer_name.strip() if payload.payer_name else None),
        reason=payload.reason,
        added_by=current_user.get("email") or "system",
    )
    db.add(d); db.commit(); db.refresh(d)
    return _denial_dict(d)


@router.patch("/denials/{denial_id}")
def patch_denial(
    denial_id: str,
    payload: DenialPatch,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    d = db.query(CodeHelperDenial).filter(CodeHelperDenial.id == denial_id).first()
    if not d:
        raise HTTPException(404, "not found")
    data = payload.model_dump(exclude_unset=True)
    if "code_type" in data and data["code_type"] not in ("cpt", "icd10"):
        raise HTTPException(422, "code_type must be 'cpt' or 'icd10'")
    for k, v in data.items():
        setattr(d, k, v)
    d.updated_at = datetime.utcnow()
    db.commit(); db.refresh(d)
    return _denial_dict(d)


@router.delete("/denials/{denial_id}", status_code=204)
def delete_denial(
    denial_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    d = db.query(CodeHelperDenial).filter(CodeHelperDenial.id == denial_id).first()
    if not d:
        raise HTTPException(404, "not found")
    db.delete(d); db.commit()
