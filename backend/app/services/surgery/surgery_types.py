"""Surgery Type catalog service: validation + CRUD + picklist serialization.

The catalog backs the surgery-intake "Surgery Name" dropdown. Each type maps a
name to one or more CPTs, a classification, optional eligible locations, and the
consent template(s) that apply.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.surgery import ConsentTemplate, SURGERY_FACILITY_VALUES
from app.models.surgery_type import SurgeryType

VALID_CLASSIFICATIONS = ("minor", "major", "office")


def _clean_cpts(raw) -> list[dict]:
    out = []
    for item in (raw or []):
        cpt = str((item or {}).get("cpt", "")).strip()
        desc = str((item or {}).get("description", "")).strip()
        if cpt or desc:
            out.append({"cpt": cpt, "description": desc})
    return out


def _validate(db: Session, payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(422, "name is required")
    cpts = _clean_cpts(payload.get("cpts"))
    if not cpts or any(not c["cpt"] for c in cpts):
        raise HTTPException(422, "at least one CPT (with a code) is required")
    classification = payload.get("classification") or "minor"
    if classification not in VALID_CLASSIFICATIONS:
        raise HTTPException(422, f"classification must be one of {VALID_CLASSIFICATIONS}")
    facilities = [f for f in (payload.get("eligible_facilities") or []) if f]
    bad = [f for f in facilities if f not in SURGERY_FACILITY_VALUES]
    if bad:
        raise HTTPException(422, f"unknown facility code(s): {bad}")
    # Drop consent-template ids that don't reference a real template.
    wanted = [str(x) for x in (payload.get("consent_template_ids") or []) if x]
    known = set()
    if wanted:
        rows = db.query(ConsentTemplate.id).filter(ConsentTemplate.id.in_(wanted)).all()
        known = {str(r[0]) for r in rows}
    consent_ids = [x for x in wanted if x in known]
    return {
        "name": name, "cpts": cpts, "classification": classification,
        "eligible_facilities": facilities, "consent_template_ids": consent_ids,
    }


def list_types(db: Session, *, include_inactive: bool = False) -> list[SurgeryType]:
    q = db.query(SurgeryType)
    if not include_inactive:
        q = q.filter(SurgeryType.active.is_(True))
    return q.order_by(SurgeryType.sort_order, SurgeryType.name).all()


def _get(db: Session, type_id: str) -> SurgeryType:
    row = db.get(SurgeryType, type_id)
    if row is None:
        raise HTTPException(404, "surgery type not found")
    return row


def create_type(db: Session, payload: dict) -> SurgeryType:
    data = _validate(db, payload)
    nxt = (db.query(SurgeryType).count())
    row = SurgeryType(sort_order=payload.get("sort_order", nxt), **data)
    db.add(row); db.commit(); db.refresh(row)
    return row


def update_type(db: Session, type_id: str, payload: dict) -> SurgeryType:
    row = _get(db, type_id)
    data = _validate(db, payload)
    for k, v in data.items():
        setattr(row, k, v)
    if "sort_order" in payload and payload["sort_order"] is not None:
        row.sort_order = payload["sort_order"]
    if "active" in payload and payload["active"] is not None:
        row.active = bool(payload["active"])
    db.commit(); db.refresh(row)
    return row


def set_active(db: Session, type_id: str, active: bool) -> SurgeryType:
    row = _get(db, type_id)
    row.active = bool(active)
    db.commit(); db.refresh(row)
    return row


def reorder(db: Session, ordered_ids: list[str]) -> None:
    for i, tid in enumerate(ordered_ids):
        row = db.get(SurgeryType, tid)
        if row is not None:
            row.sort_order = i
    db.commit()


def as_picklist(types: list[SurgeryType]) -> list[dict]:
    return [{
        "id": str(t.id),
        "name": t.name,
        "cpts": t.cpts or [],
        "classification": t.classification,
        "eligible_facilities": t.eligible_facilities or [],
        "consent_template_ids": t.consent_template_ids or [],
    } for t in types]
