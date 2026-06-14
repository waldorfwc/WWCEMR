"""Admin CRUD for ConsentTemplate.

Surgery schedulers register a DocuSign template per procedure here.
Templates are matched at consent-send time by procedure keywords +
optional facility + optional insurance keywords. Supplemental forms
(Medicaid sterilization, etc.) attach in addition to the primary
procedure-matched template.
"""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier

router = APIRouter(prefix="/consent-templates", tags=["consent-templates"])


class ConsentTemplateIn(BaseModel):
    name: str
    boldsign_template_id: str
    # CPT-based primary match — most reliable. When set, the matcher uses CPT
    # membership instead of substring keywords. Same CPT at different facilities
    # (D&C office vs hospital) resolves through facility_match.
    cpt_codes: list[str] = []
    # Substring keywords on the procedure description. Used as a fallback when
    # cpt_codes is empty.
    procedure_match: list[str] = []
    # facility_match: accept a single code (legacy) or a list. The save handler
    # normalises into a JSON list.
    facility_match: Optional[object] = None
    insurance_match: list[str] = []
    is_supplemental: bool = False
    min_days_before_surgery: Optional[int] = None
    notes: Optional[str] = None
    is_active: bool = True
    category: Literal["surgical", "larc"] = "surgical"


def _normalize_cpt_codes(raw) -> list[str]:
    """CPT codes are short numeric/alphanumeric strings — coerce a list (or
    single string) into a deduplicated list of trimmed values."""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [c for c in raw.replace(",", " ").split() if c.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for c in raw:
        v = str(c).strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _normalize_facility_match(raw):
    """Coerce facility_match payload into a JSON list of codes.
    The Pydantic field accepts a single string (current FE dropdown) but the
    matcher expects a list — wrap, and tolerate already-list input."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(c).strip().lower() for c in raw if str(c).strip()]
    s = str(raw).strip().lower()
    return [s] if s else []


def _to_dict(t: ConsentTemplate, in_use_count: int = 0) -> dict:
    return {
        "id": str(t.id),
        "name": t.name,
        "boldsign_template_id": t.boldsign_template_id,
        "cpt_codes": t.cpt_codes or [],
        "procedure_match": t.procedure_match or [],
        "facility_match": t.facility_match,
        "insurance_match": t.insurance_match or [],
        "is_supplemental": bool(t.is_supplemental),
        "min_days_before_surgery": t.min_days_before_surgery,
        "notes": t.notes,
        "is_active": bool(t.is_active),
        "category": t.category or "surgical",
        "in_use_count": in_use_count,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.get("")
def list_templates(category: Optional[str] = None,
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    q = db.query(ConsentTemplate)
    if category in {"surgical", "larc"}:
        q = q.filter(ConsentTemplate.category == category)
    rows = (q.order_by(ConsentTemplate.is_supplemental, ConsentTemplate.name)
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
                     current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    bs_id = (payload.boldsign_template_id or "").strip()
    if not payload.name.strip() or not bs_id:
        raise HTTPException(status_code=400,
                            detail="name and boldsign_template_id are required")
    t = ConsentTemplate(
        name=payload.name.strip(),
        boldsign_template_id=bs_id,
        cpt_codes=_normalize_cpt_codes(payload.cpt_codes),
        procedure_match=[p.strip().lower() for p in payload.procedure_match if p.strip()],
        # facility_match is a JSON list of facility codes; empty list = any facility
        facility_match=_normalize_facility_match(payload.facility_match),
        insurance_match=[p.strip().lower() for p in payload.insurance_match if p.strip()],
        is_supplemental=bool(payload.is_supplemental),
        min_days_before_surgery=payload.min_days_before_surgery,
        notes=payload.notes,
        is_active=bool(payload.is_active),
        category=payload.category,
    )
    db.add(t); db.commit(); db.refresh(t)
    return _to_dict(t)


@router.put("/{template_id}")
def update_template(template_id: str, payload: ConsentTemplateIn,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    t = db.query(ConsentTemplate).filter(ConsentTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    bs_id = (payload.boldsign_template_id or "").strip()
    if not bs_id:
        raise HTTPException(status_code=400,
                            detail="boldsign_template_id is required")
    t.name = payload.name.strip()
    t.boldsign_template_id = bs_id
    t.cpt_codes = _normalize_cpt_codes(payload.cpt_codes)
    t.procedure_match = [p.strip().lower() for p in payload.procedure_match if p.strip()]
    t.facility_match = _normalize_facility_match(payload.facility_match)
    t.insurance_match = [p.strip().lower() for p in payload.insurance_match if p.strip()]
    t.is_supplemental = bool(payload.is_supplemental)
    t.min_days_before_surgery = payload.min_days_before_surgery
    t.notes = payload.notes
    t.is_active = bool(payload.is_active)
    t.category = payload.category
    t.updated_at = now_utc_naive()
    db.commit(); db.refresh(t)
    return _to_dict(t)


@router.delete("/{template_id}")
def delete_template(template_id: str,
                     db: Session = Depends(get_db),
                     current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
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
    procedure: str = ""
    cpt: Optional[str] = None
    facility: Optional[str] = None
    primary_insurance: Optional[str] = None


@router.post("/test-match")
def test_match(payload: TemplateTestPayload,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))):
    """Given a hypothetical procedure / facility / insurance, return the
    templates that would match. Used by the admin form's 'Test match'
    button so staff can verify their setup before saving."""
    from app.services.consent_template_matcher import (
        _procedure_template_matches, _facility_template_matches,
        _insurance_template_matches,
    )
    rows = (db.query(ConsentTemplate)
              .filter(ConsentTemplate.is_active.is_(True)).all())
    proc = {"cpt": (payload.cpt or "").strip(),
            "description": (payload.procedure or "").strip()}
    out = []
    for t in rows:
        p_ok = _procedure_template_matches(t, proc)
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
    current_user: dict = Depends(requires_tier(Module.SURGERY, Tier.MANAGE))
):
    """Pull the live list of templates from BoldSign so admins can pick a
    templateId from a dropdown instead of hand-typing it.

    BoldSign endpoint: GET /v1/template/list
    Header: X-API-KEY.
    """
    import os, httpx, logging
    log = logging.getLogger(__name__)
    api_key = os.environ.get("BOLDSIGN_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503,
                            detail="BoldSign API key not configured")
    try:
        r = httpx.get("https://api.boldsign.com/v1/template/list",
                        headers={"X-API-KEY": api_key},
                        timeout=30,
                        params={"Page": 1, "PageSize": 100})
    except Exception as exc:
        log.exception("BoldSign template list — httpx call failed")
        raise HTTPException(status_code=502,
                            detail=f"BoldSign request error: {exc}")
    if r.status_code != 200:
        log.warning("BoldSign returned %s: %s", r.status_code, r.text[:400])
        raise HTTPException(status_code=502,
                            detail=f"BoldSign returned {r.status_code}: {r.text[:200]}")
    try:
        data = r.json() or {}
    except Exception as exc:
        log.exception("BoldSign returned non-JSON: %s", r.text[:200])
        raise HTTPException(status_code=502,
                            detail=f"BoldSign returned non-JSON: {exc}")
    rows = data.get("result") or data.get("Result") or []
    out = []
    for t in rows:
        try:
            cb = t.get("createdBy")
            owner = (cb.get("name") if isinstance(cb, dict) else cb) or None
            out.append({
                "template_id":   t.get("documentId") or t.get("templateId"),
                "name":          t.get("title") or t.get("messageTitle") or t.get("name"),
                "owner":         owner,
                "last_modified": t.get("createdDate"),
            })
        except Exception as exc:
            log.exception("BoldSign row parse error; row=%s", t)
            # keep going — return whatever we successfully parsed
            continue
    return out


