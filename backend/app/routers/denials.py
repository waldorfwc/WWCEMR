from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, func
from typing import Optional
from datetime import date

from app.database import get_db
from app.models.denial import Denial, DenialStatus
from app.models.claim import Claim
from app.services.audit_service import log_action
from app.services.denial_analyzer import get_denial_summary

router = APIRouter(prefix="/denials", tags=["denials"])


@router.get("")
def list_denials(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    category: Optional[str] = None,
    urgent_only: bool = False,
    write_off_only: bool = False,
    page: int = 1,
    per_page: int = 50,
):
    q = db.query(Denial).options(joinedload(Denial.claim))
    if status:
        q = q.filter(Denial.status == status)
    if category:
        q = q.filter(Denial.category == category)
    if urgent_only:
        q = q.filter(
            Denial.status == DenialStatus.OPEN,
            Denial.appeal_deadline <= date.today().__class__.today().__add__(__import__("datetime").timedelta(days=30)),
            Denial.appeal_deadline >= date.today(),
        )
    if write_off_only:
        q = q.filter(Denial.write_off_recommended == True)

    total = q.count()
    denials = q.order_by(Denial.appeal_deadline.asc().nullslast()).offset((page - 1) * per_page).limit(per_page).all()

    return {
        "total": total,
        "denials": [_to_dict(d) for d in denials],
    }


@router.get("/summary")
def denial_summary(db: Session = Depends(get_db)):
    return get_denial_summary(db)


@router.get("/{denial_id}")
def get_denial(denial_id: str, db: Session = Depends(get_db)):
    denial = db.query(Denial).options(
        joinedload(Denial.claim),
        joinedload(Denial.appeals),
    ).filter(Denial.id == denial_id).first()
    if not denial:
        raise HTTPException(status_code=404, detail="Denial not found")
    log_action(db, "VIEW", "denial", resource_id=denial_id)
    return _to_dict(denial, detailed=True)


@router.patch("/{denial_id}")
def update_denial(denial_id: str, data: dict, db: Session = Depends(get_db)):
    denial = db.query(Denial).filter(Denial.id == denial_id).first()
    if not denial:
        raise HTTPException(status_code=404, detail="Denial not found")
    allowed = ["status", "notes", "recommended_action", "write_off_recommended",
               "appeal_submitted_date", "appeal_decision_date", "appeal_decision"]
    for k, v in data.items():
        if k in allowed:
            setattr(denial, k, v)
    db.commit()
    log_action(db, "UPDATE", "denial", resource_id=denial_id, new_values=data)
    return _to_dict(denial)


def _to_dict(denial: Denial, detailed: bool = False) -> dict:
    d = {
        "id": str(denial.id),
        "claim_id": str(denial.claim_id) if denial.claim_id else None,
        "carc_code": denial.carc_code,
        "carc_description": denial.carc_description,
        "rarc_code": denial.rarc_code,
        "rarc_description": denial.rarc_description,
        "group_code": denial.group_code,
        "category": denial.category.value if denial.category else "other",
        "denied_amount": float(denial.denied_amount or 0),
        "denial_date": str(denial.denial_date) if denial.denial_date else None,
        "status": denial.status.value if denial.status else "open",
        "appeal_deadline": str(denial.appeal_deadline) if denial.appeal_deadline else None,
        "appeal_level": denial.appeal_level,
        "appealable": denial.appealable,
        "write_off_recommended": denial.write_off_recommended,
        "write_off_reason": denial.write_off_reason,
        "recommended_action": denial.recommended_action,
        "notes": denial.notes,
        "appeal_submitted_date": str(denial.appeal_submitted_date) if denial.appeal_submitted_date else None,
        "appeal_decision": denial.appeal_decision,
    }
    if detailed and denial.claim:
        c = denial.claim
        d["claim"] = {
            "claim_number": c.claim_number,
            "payer_name": c.payer_name,
            "date_of_service_from": str(c.date_of_service_from) if c.date_of_service_from else None,
            "billed_amount": float(c.billed_amount or 0),
            "patient_id": str(c.patient_id) if c.patient_id else None,
        }
        d["appeals"] = [
            {
                "id": str(a.id),
                "level": a.level,
                "status": a.status.value if a.status else "draft",
                "deadline": str(a.deadline) if a.deadline else None,
                "submitted_date": str(a.submitted_date) if a.submitted_date else None,
            }
            for a in denial.appeals
        ]
    return d
