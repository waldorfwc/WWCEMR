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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.code_helper import CodeHelperDenial, CodeHelperRequest  # noqa: F401
from app.routers.auth import require_permission


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
    current_user: dict = Depends(require_permission("claim:read")),
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
    current_user: dict = Depends(require_permission("claim:edit")),
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
    current_user: dict = Depends(require_permission("claim:edit")),
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
    current_user: dict = Depends(require_permission("user:manage")),
):
    d = db.query(CodeHelperDenial).filter(CodeHelperDenial.id == denial_id).first()
    if not d:
        raise HTTPException(404, "not found")
    db.delete(d); db.commit()


# ─── Requests ─────────────────────────────────────────────────────────────────

from fastapi import File, Form, UploadFile  # noqa: E402 (after router definition)

from app.services.code_helper_ai import generate_codes  # noqa: E402
from app.services.code_helper_match import match_patient, MatchKind  # noqa: E402
from app.services.audit_service import log_action  # noqa: E402


def _serialize_request(r: CodeHelperRequest) -> dict:
    return {
        "id":           str(r.id),
        "requested_at": r.requested_at.isoformat() if r.requested_at else None,
        "requested_by": r.requested_by,
        "note_text":    r.note_text,
        "source_pdf_storage_filename": r.source_pdf_storage_filename,
        "payer_name":   r.payer_name,
        "patient_name": r.patient_name,
        "patient_dob":  r.patient_dob.isoformat() if r.patient_dob else None,
        "patient_id":   r.patient_id,
        "cpt_codes":    r.cpt_codes,
        "icd10_codes":  r.icd10_codes,
        "ai_model":     r.ai_model,
        "ai_input_tokens":  r.ai_input_tokens,
        "ai_output_tokens": r.ai_output_tokens,
        "error":        r.error,
    }


@router.post("/requests", status_code=201)
def create_request(
    note_text:  Optional[str]        = Form(None),
    note_pdf:   Optional[UploadFile] = File(None),
    payer_name: Optional[str]        = Form(None),
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("claim:edit")),
):
    if not note_text and not note_pdf:
        raise HTTPException(422, "Provide note_text or note_pdf")

    note_pdf_b64 = None
    if note_pdf is not None:
        body = note_pdf.file.read()
        if len(body) > 10 * 1024 * 1024:
            raise HTTPException(422, "PDF too large (>10 MB)")
        if not body.startswith(b"%PDF"):
            raise HTTPException(422, "Not a valid PDF (missing %PDF header)")
        import base64
        note_pdf_b64 = base64.b64encode(body).decode("ascii")

    # Pull active, payer-relevant denials.
    q = db.query(CodeHelperDenial).filter(CodeHelperDenial.is_active.is_(True))
    if payer_name:
        q = q.filter(or_(CodeHelperDenial.payer_name == payer_name,
                          CodeHelperDenial.payer_name.is_(None)))
    else:
        q = q.filter(CodeHelperDenial.payer_name.is_(None))
    active_denials = [
        {"code": d.code, "code_type": d.code_type,
         "payer_name": d.payer_name, "reason": d.reason}
        for d in q.all()
    ]

    try:
        ai_result, usage, model = generate_codes(
            note_text=note_text, note_pdf_b64=note_pdf_b64,
            payer=payer_name, active_denials=active_denials,
        )
    except Exception as e:
        # AI call failed — save the row with the error and 502 out.
        row = CodeHelperRequest(
            requested_by=user.get("email") or "system",
            note_text=note_text, payer_name=payer_name,
            cpt_codes=[], icd10_codes=[],
            ai_model="claude-opus-4-7",
            error=str(e),
        )
        db.add(row); db.commit(); db.refresh(row)
        raise HTTPException(502, f"AI call failed: {e}")

    match = match_patient(db, name=ai_result.patient_name,
                           dob=ai_result.patient_dob)

    row = CodeHelperRequest(
        requested_by=user.get("email") or "system",
        note_text=note_text, payer_name=payer_name,
        patient_name=ai_result.patient_name,
        patient_dob=ai_result.patient_dob,
        patient_id=(match.patient_id if match.kind == MatchKind.ONE else None),
        cpt_codes=[c.model_dump(mode="json") for c in ai_result.cpt_codes],
        icd10_codes=[i.model_dump(mode="json") for i in ai_result.icd10_codes],
        ai_model=model,
        ai_input_tokens=usage.get("input_tokens"),
        ai_output_tokens=usage.get("output_tokens"),
    )
    db.add(row); db.commit(); db.refresh(row)

    log_action(
        db,
        "code_helper_generated",
        "code_helper_request",
        resource_id=str(row.id),
        patient_id=row.patient_id,
        user_name=user.get("email"),
        description=(f"Generated codes for {row.patient_name or '?'} "
                      f"(payer={row.payer_name or '—'}, "
                      f"cpts={len(row.cpt_codes)})"),
    )

    return _serialize_request(row)


from datetime import date as _date  # noqa: E402


class RequestPatch(BaseModel):
    patient_name: Optional[str]   = None
    patient_dob:  Optional[_date] = None
    patient_id:   Optional[str]   = None


@router.get("/requests")
def list_requests(
    db: Session = Depends(get_db),
    page:     int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    patient_id: Optional[str] = None,
    payer:      Optional[str] = None,
    _user = Depends(require_permission("claim:read")),
):
    q = db.query(CodeHelperRequest)
    if patient_id:
        q = q.filter(CodeHelperRequest.patient_id == patient_id)
    if payer:
        q = q.filter(CodeHelperRequest.payer_name == payer)
    total = q.count()
    rows = (q.order_by(CodeHelperRequest.requested_at.desc())
             .offset((page - 1) * per_page).limit(per_page).all())
    return {"total": total, "page": page, "per_page": per_page,
            "requests": [_serialize_request(r) for r in rows]}


@router.get("/requests/{request_id}")
def get_request(
    request_id: str,
    db: Session = Depends(get_db),
    _user = Depends(require_permission("claim:read")),
):
    r = db.query(CodeHelperRequest).filter(CodeHelperRequest.id == request_id).first()
    if not r:
        raise HTTPException(404, "not found")
    return _serialize_request(r)


@router.patch("/requests/{request_id}")
def patch_request(
    request_id: str, payload: RequestPatch,
    db: Session = Depends(get_db),
    _user = Depends(require_permission("claim:edit")),
):
    r = db.query(CodeHelperRequest).filter(CodeHelperRequest.id == request_id).first()
    if not r:
        raise HTTPException(404, "not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(r, k, v)
    db.commit(); db.refresh(r)
    return _serialize_request(r)


@router.delete("/requests/{request_id}", status_code=204)
def delete_request(
    request_id: str,
    db: Session = Depends(get_db),
    _user = Depends(require_permission("user:manage")),
):
    r = db.query(CodeHelperRequest).filter(CodeHelperRequest.id == request_id).first()
    if not r:
        raise HTTPException(404, "not found")
    db.delete(r); db.commit()
