"""Insurance Contacts — billing-team directory of insurance companies."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.insurance_contact import (
    InsuranceContact, InsuranceContactHistory,
)
from app.routers.auth import require_permission
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier


router = APIRouter(prefix="/insurance-contacts", tags=["insurance-contacts"])


# ─── Pydantic shapes ────────────────────────────────────────────────

class LabeledLink(BaseModel):
    label: str = Field(default="", max_length=120)
    url:   str = Field(default="", max_length=500)


class LabeledPhone(BaseModel):
    label:  str = Field(default="", max_length=120)
    number: str = Field(default="", max_length=60)


class ContactIn(BaseModel):
    company:      str
    claims_links: list[LabeledLink] = Field(default_factory=list)
    phones:       list[LabeledPhone] = Field(default_factory=list)
    notes:        Optional[str] = None


class ContactPatch(BaseModel):
    company:      Optional[str] = None
    claims_links: Optional[list[LabeledLink]] = None
    phones:       Optional[list[LabeledPhone]] = None
    notes:        Optional[str] = None


def _dump(c: InsuranceContact) -> dict:
    return {
        "id":            str(c.id),
        "company":       c.company,
        "claims_links":  list(c.claims_links or []),
        "phones":        list(c.phones or []),
        "notes":         c.notes,
        "created_by":    c.created_by,
        "created_at":    c.created_at.isoformat() if c.created_at else None,
        "updated_by":    c.updated_by,
        "updated_at":    c.updated_at.isoformat() if c.updated_at else None,
    }


def _log(db: Session, contact_id, actor: str, action: str,
         before: Optional[dict] = None, after: Optional[dict] = None) -> None:
    db.add(InsuranceContactHistory(
        contact_id=contact_id, actor=actor, action=action,
        before=before, after=after,
    ))


# ─── List + create ──────────────────────────────────────────────────

@router.get("")
def list_contacts(
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.INSURANCE_CONTACTS, Tier.VIEW)),
):
    rows = (db.query(InsuranceContact)
              .order_by(InsuranceContact.company.asc())
              .all())
    return {"contacts": [_dump(c) for c in rows]}


@router.post("", status_code=201)
def create_contact(
    payload: ContactIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.INSURANCE_CONTACTS, Tier.WORK)),
):
    company = (payload.company or "").strip()
    if not company:
        raise HTTPException(status_code=422, detail="company is required")
    actor = current_user.get("email") or "system"
    c = InsuranceContact(
        company=company,
        claims_links=[l.model_dump() for l in payload.claims_links],
        phones=[p.model_dump() for p in payload.phones],
        notes=(payload.notes or None),
        created_by=actor,
        updated_by=actor,
    )
    db.add(c)
    db.flush()
    _log(db, c.id, actor, "created", after=_dump(c))
    db.commit(); db.refresh(c)
    return _dump(c)


# ─── Detail / patch / delete ────────────────────────────────────────

def _load(db: Session, contact_id: str) -> InsuranceContact:
    c = db.query(InsuranceContact).filter(InsuranceContact.id == contact_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contact not found")
    return c


@router.get("/{contact_id}")
def get_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.INSURANCE_CONTACTS, Tier.VIEW)),
):
    return _dump(_load(db, contact_id))


@router.patch("/{contact_id}")
def patch_contact(
    contact_id: str, payload: ContactPatch,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.INSURANCE_CONTACTS, Tier.WORK)),
):
    c = _load(db, contact_id)
    before = _dump(c)
    actor = current_user.get("email") or "system"
    data = payload.model_dump(exclude_unset=True)

    if "company" in data:
        v = (data["company"] or "").strip()
        if not v:
            raise HTTPException(status_code=422, detail="company cannot be empty")
        c.company = v
    if "claims_links" in data:
        c.claims_links = [
            {"label": (l.get("label") or "").strip(),
             "url":   (l.get("url")   or "").strip()}
            for l in (data["claims_links"] or [])
            if (l.get("label") or l.get("url"))
        ]
    if "phones" in data:
        c.phones = [
            {"label":  (p.get("label")  or "").strip(),
             "number": (p.get("number") or "").strip()}
            for p in (data["phones"] or [])
            if (p.get("label") or p.get("number"))
        ]
    if "notes" in data:
        c.notes = (data["notes"] or "").strip() or None
    c.updated_by = actor

    after = _dump(c)
    _log(db, c.id, actor, "updated", before=before, after=after)
    db.commit(); db.refresh(c)
    return _dump(c)


@router.delete("/{contact_id}", status_code=204)
def delete_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.INSURANCE_CONTACTS, Tier.MANAGE)),
):
    c = _load(db, contact_id)
    before = _dump(c)
    actor = current_user.get("email") or "system"
    _log(db, c.id, actor, "deleted", before=before)
    db.delete(c)
    db.commit()
    return None
