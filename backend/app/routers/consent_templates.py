"""Admin CRUD for ConsentTemplate.

Surgery schedulers register a DocuSign template per procedure here.
Templates are matched at consent-send time by procedure keywords +
optional facility + optional insurance keywords. Supplemental forms
(Medicaid sterilization, etc.) attach in addition to the primary
procedure-matched template.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
from app.routers.auth import require_permission

router = APIRouter(prefix="/consent-templates", tags=["consent-templates"])


class ConsentTemplateIn(BaseModel):
    name: str
    # Primary field (BoldSign). Legacy `docusign_template_id` still accepted
    # so older clients don't 422 — it's mirrored into boldsign_template_id
    # if the BoldSign field is missing.
    boldsign_template_id: Optional[str] = None
    docusign_template_id: Optional[str] = None
    procedure_match: list[str] = []
    facility_match: Optional[str] = None
    insurance_match: list[str] = []
    is_supplemental: bool = False
    min_days_before_surgery: Optional[int] = None
    notes: Optional[str] = None
    is_active: bool = True


def _to_dict(t: ConsentTemplate, in_use_count: int = 0) -> dict:
    return {
        "id": str(t.id),
        "name": t.name,
        "boldsign_template_id": t.boldsign_template_id,
        "docusign_template_id": t.docusign_template_id,
        "procedure_match": t.procedure_match or [],
        "facility_match": t.facility_match,
        "insurance_match": t.insurance_match or [],
        "is_supplemental": bool(t.is_supplemental),
        "min_days_before_surgery": t.min_days_before_surgery,
        "notes": t.notes,
        "is_active": bool(t.is_active),
        "in_use_count": in_use_count,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.get("")
def list_templates(db: Session = Depends(get_db),
                    current_user: dict = Depends(require_permission("surgery:manage"))):
    rows = (db.query(ConsentTemplate)
              .order_by(ConsentTemplate.is_supplemental, ConsentTemplate.name)
              .all())
    # Count envelopes per template (so admins can't surprise-delete a template
    # with surgeries depending on it)
    counts: dict = {}
    for tid, in db.query(SurgeryConsentEnvelope.template_id).all():
        counts[tid] = counts.get(tid, 0) + 1
    return [_to_dict(t, counts.get(t.id, 0)) for t in rows]


@router.post("")
def create_template(payload: ConsentTemplateIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(require_permission("surgery:manage"))):
    bs_id = (payload.boldsign_template_id or payload.docusign_template_id or "").strip()
    if not payload.name.strip() or not bs_id:
        raise HTTPException(status_code=400,
                            detail="name and boldsign_template_id are required")
    t = ConsentTemplate(
        name=payload.name.strip(),
        boldsign_template_id=bs_id,
        procedure_match=[p.strip().lower() for p in payload.procedure_match if p.strip()],
        facility_match=(payload.facility_match or None) or None,
        insurance_match=[p.strip().lower() for p in payload.insurance_match if p.strip()],
        is_supplemental=bool(payload.is_supplemental),
        min_days_before_surgery=payload.min_days_before_surgery,
        notes=payload.notes,
        is_active=bool(payload.is_active),
    )
    db.add(t); db.commit(); db.refresh(t)
    return _to_dict(t)


@router.put("/{template_id}")
def update_template(template_id: str, payload: ConsentTemplateIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(require_permission("surgery:manage"))):
    t = db.query(ConsentTemplate).filter(ConsentTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    bs_id = (payload.boldsign_template_id or payload.docusign_template_id or "").strip()
    if not bs_id:
        raise HTTPException(status_code=400,
                            detail="boldsign_template_id is required")
    t.name = payload.name.strip()
    t.boldsign_template_id = bs_id
    t.procedure_match = [p.strip().lower() for p in payload.procedure_match if p.strip()]
    t.facility_match = (payload.facility_match or None) or None
    t.insurance_match = [p.strip().lower() for p in payload.insurance_match if p.strip()]
    t.is_supplemental = bool(payload.is_supplemental)
    t.min_days_before_surgery = payload.min_days_before_surgery
    t.notes = payload.notes
    t.is_active = bool(payload.is_active)
    t.updated_at = datetime.utcnow()
    db.commit(); db.refresh(t)
    return _to_dict(t)


@router.delete("/{template_id}")
def delete_template(template_id: str,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(require_permission("surgery:manage"))):
    t = db.query(ConsentTemplate).filter(ConsentTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    in_use = (db.query(SurgeryConsentEnvelope)
                .filter(SurgeryConsentEnvelope.template_id == template_id)
                .count())
    if in_use > 0:
        raise HTTPException(
            status_code=400,
            detail=(f"Cannot delete: {in_use} surgery envelope(s) reference this template. "
                    "Set is_active=false to retire it instead."),
        )
    db.delete(t); db.commit()
    return {"ok": True}


class TemplateTestPayload(BaseModel):
    procedure: str
    facility: Optional[str] = None
    primary_insurance: Optional[str] = None


@router.post("/test-match")
def test_match(payload: TemplateTestPayload,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(require_permission("surgery:manage"))):
    """Given a hypothetical procedure / facility / insurance, return the
    templates that would match. Used by the admin form's 'Test match'
    button so staff can verify their setup before saving."""
    from app.services.consent_template_matcher import (
        _procedure_template_matches, _facility_template_matches,
        _insurance_template_matches,
    )
    rows = (db.query(ConsentTemplate)
              .filter(ConsentTemplate.is_active.is_(True)).all())
    out = []
    for t in rows:
        p_ok = _procedure_template_matches(t, payload.procedure)
        f_ok = _facility_template_matches(t, payload.facility)
        i_ok = _insurance_template_matches(t, payload.primary_insurance)
        out.append({
            "template_id": str(t.id),
            "name": t.name,
            "is_supplemental": bool(t.is_supplemental),
            "matches": p_ok and f_ok and i_ok,
            "procedure_match_ok": p_ok,
            "facility_match_ok": f_ok,
            "insurance_match_ok": i_ok,
        })
    return out


@router.get("/boldsign-templates")
def list_boldsign_templates(
    current_user: dict = Depends(require_permission("surgery:manage"))
):
    """Pull the live list of templates from BoldSign so admins can pick a
    templateId from a dropdown instead of hand-typing it.

    BoldSign endpoint: GET /v1/template/list
    Header: X-API-KEY.
    """
    import os, httpx
    api_key = os.environ.get("BOLDSIGN_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503,
                            detail="BoldSign API key not configured")
    r = httpx.get("https://api.boldsign.com/v1/template/list",
                    headers={"X-API-KEY": api_key},
                    timeout=30,
                    params={"Page": 1, "PageSize": 200})
    if r.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"BoldSign returned {r.status_code}: {r.text[:200]}")
    data = r.json() or {}
    out = []
    for t in (data.get("result") or data.get("Result") or []):
        out.append({
            "template_id":   t.get("documentId") or t.get("templateId"),
            "name":          t.get("title") or t.get("name"),
            "owner":         (t.get("createdBy") or {}).get("name")
                              if isinstance(t.get("createdBy"), dict)
                              else t.get("createdBy"),
            "last_modified": t.get("createdDate"),
        })
    return out


@router.get("/docusign-templates")
def list_docusign_templates(
    current_user: dict = Depends(require_permission("surgery:manage"))
):
    """Legacy DocuSign listing. Kept so old client builds don't 404 while
    the BoldSign-only UI rolls out. BoldSign is the primary surface — use
    /boldsign-templates instead."""
    import httpx
    try:
        from app.services.docusign_client import auth_headers, envelopes_base_url
    except Exception:
        raise HTTPException(status_code=410, detail="DocuSign integration retired")
    r = httpx.get(f"{envelopes_base_url()}/templates", headers=auth_headers(),
                  timeout=30, params={"count": 200})
    if r.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"DocuSign returned {r.status_code}: {r.text[:200]}")
    data = r.json()
    return [
        {
            "template_id": t.get("templateId"),
            "name": t.get("name"),
            "owner": (t.get("owner") or {}).get("userName"),
            "last_modified": t.get("lastModified"),
        }
        for t in data.get("envelopeTemplates", [])
    ]
