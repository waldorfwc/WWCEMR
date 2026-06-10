from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel
from typing import Optional
from datetime import date
import os

from app.database import get_db
from app.models.appeal import Appeal, AppealStatus
from app.models.denial import Denial, DenialStatus
from app.models.claim import Claim
from app.models.patient import Patient
from app.services.appeal_generator import generate_appeal_letter_sync
from app.services.audit_service import log_action
from app.routers.auth import get_current_user
from app.config import settings

router = APIRouter(prefix="/appeals", tags=["appeals"])


class GenerateAppealRequest(BaseModel):
    denial_id: str
    additional_notes: Optional[str] = None
    practice_name: Optional[str] = None
    practice_address: Optional[str] = None
    practice_phone: Optional[str] = None
    practice_npi: Optional[str] = None


@router.post("/generate")
def generate_appeal(req: GenerateAppealRequest, db: Session = Depends(get_db),
                     current_user: dict = Depends(get_current_user)):
    """Generate an AI appeal letter for a denial."""
    denial = db.query(Denial).options(joinedload(Denial.claim)).filter(Denial.id == req.denial_id).first()
    if not denial:
        raise HTTPException(status_code=404, detail="Denial not found")

    claim = denial.claim
    if not claim:
        raise HTTPException(status_code=404, detail="Associated claim not found")

    # Get patient name
    patient_name = "Unknown Patient"
    if claim.patient_id:
        patient = db.query(Patient).filter(Patient.id == claim.patient_id).first()
        if patient:
            patient_name = patient.full_name

    practice_info = {
        "name": req.practice_name or settings.practice_name,
        "address": req.practice_address or settings.practice_address,
        "phone": req.practice_phone or settings.practice_phone,
        "npi": req.practice_npi or settings.practice_npi,
    }

    result = generate_appeal_letter_sync(
        denial=denial,
        claim=claim,
        patient_name=patient_name,
        practice_info=practice_info,
        additional_notes=req.additional_notes or "",
    )

    # Create appeal record
    appeal = Appeal(
        denial_id=denial.id,
        level=denial.appeal_level,
        status=AppealStatus.DRAFT,
        letter_subject=result["subject"],
        letter_body=result["body"],
        deadline=denial.appeal_deadline,
        generated_by_ai=True,
        ai_model=result["model_used"],
    )
    db.add(appeal)

    # Update denial status
    denial.status = DenialStatus.APPEALING

    db.commit()
    db.refresh(appeal)

    log_action(db, "GENERATE_APPEAL", "appeal", actor=current_user,
               resource_id=str(appeal.id),
               description=f"AI appeal letter generated for denial {req.denial_id}")

    return {
        "appeal_id": str(appeal.id),
        "subject": result["subject"],
        "body": result["body"],
        "denial_category": result["denial_category"],
        "model_used": result["model_used"],
    }


@router.get("")
def list_appeals(db: Session = Depends(get_db)):
    appeals = db.query(Appeal).order_by(Appeal.created_at.desc()).limit(100).all()
    return [_to_dict(a) for a in appeals]


@router.get("/{appeal_id}")
def get_appeal(appeal_id: str, db: Session = Depends(get_db),
                current_user: dict = Depends(get_current_user)):
    appeal = db.query(Appeal).filter(Appeal.id == appeal_id).first()
    if not appeal:
        raise HTTPException(status_code=404, detail="Appeal not found")
    log_action(db, "VIEW", "appeal", actor=current_user, resource_id=appeal_id)
    return _to_dict(appeal, detailed=True)


@router.patch("/{appeal_id}")
def update_appeal(appeal_id: str, data: dict, db: Session = Depends(get_db),
                   current_user: dict = Depends(get_current_user)):
    appeal = db.query(Appeal).filter(Appeal.id == appeal_id).first()
    if not appeal:
        raise HTTPException(status_code=404, detail="Appeal not found")
    allowed = ["status", "letter_body", "letter_subject", "submitted_date",
               "decision_date", "decision_notes", "supporting_docs"]
    for k, v in data.items():
        if k in allowed:
            setattr(appeal, k, v)
    if data.get("status") == "submitted" and not appeal.submitted_date:
        appeal.submitted_date = date.today()
        if appeal.denial:
            appeal.denial.appeal_submitted_date = date.today()
    db.commit()
    log_action(db, "UPDATE", "appeal", actor=current_user, resource_id=appeal_id, new_values=data)
    return _to_dict(appeal)


@router.get("/{appeal_id}/download")
def download_appeal_letter(appeal_id: str, db: Session = Depends(get_db),
                            current_user: dict = Depends(get_current_user)):
    """Download the appeal letter as a plain text file."""
    from fastapi.responses import Response
    appeal = db.query(Appeal).filter(Appeal.id == appeal_id).first()
    if not appeal:
        raise HTTPException(status_code=404, detail="Appeal not found")
    log_action(db, "EXPORT", "appeal", actor=current_user, resource_id=appeal_id)
    return Response(
        content=appeal.letter_body or "",
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=appeal_{appeal_id}.txt"},
    )


def _to_dict(appeal: Appeal, detailed: bool = False) -> dict:
    d = {
        "id": str(appeal.id),
        "denial_id": str(appeal.denial_id) if appeal.denial_id else None,
        "level": appeal.level,
        "status": appeal.status.value if appeal.status else "draft",
        "letter_subject": appeal.letter_subject,
        "deadline": str(appeal.deadline) if appeal.deadline else None,
        "submitted_date": str(appeal.submitted_date) if appeal.submitted_date else None,
        "decision_date": str(appeal.decision_date) if appeal.decision_date else None,
        "decision_notes": appeal.decision_notes,
        "generated_by_ai": appeal.generated_by_ai,
        "created_at": appeal.created_at.isoformat() if appeal.created_at else None,
    }
    if detailed:
        d["letter_body"] = appeal.letter_body
    return d
