from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
import os

from app.database import get_db
from app.models.claim import Claim
from app.models.patient import Patient
from app.services.eob_generator import generate_eob_pdf
from app.services.audit_service import log_action
from app.routers.auth import get_current_user
from app.config import settings

router = APIRouter(prefix="/eob", tags=["eob"])


@router.get("/{claim_id}/pdf")
def get_eob_pdf(claim_id: str, db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    """Generate and return an EOB PDF for a claim."""
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    patient_info = {}
    if claim.patient_id:
        patient = db.query(Patient).filter(Patient.id == claim.patient_id).first()
        if patient:
            patient_info = {
                "full_name": patient.full_name,
                "date_of_birth": str(patient.date_of_birth) if patient.date_of_birth else "",
                "patient_id": patient.patient_id,
            }
    if not patient_info:
        # Fallback to ERA data
        name_parts = []
        if hasattr(claim, "_era_patient_first"):
            name_parts = [claim._era_patient_first, claim._era_patient_last]
        patient_info = {"full_name": " ".join(filter(None, name_parts)) or "Unknown"}

    practice_info = {
        "name": settings.practice_name,
        "address": settings.practice_address,
        "phone": settings.practice_phone,
        "npi": settings.practice_npi,
    }

    claim_data = {
        "claim_number": claim.claim_number,
        "payer_claim_number": claim.payer_claim_number,
        "date_of_service_from": claim.date_of_service_from,
        "date_of_service_to": claim.date_of_service_to,
        "payer_name": claim.payer_name,
        "subscriber_id": claim.subscriber_id,
        "group_number": claim.group_number,
        "check_number": claim.check_number,
        "check_date": claim.check_date,
        "rendering_provider_name": claim.rendering_provider_name,
        "billed_amount": claim.billed_amount,
        "allowed_amount": claim.allowed_amount,
        "paid_amount": claim.paid_amount,
        "contractual_adjustment": claim.contractual_adjustment,
        "other_adjustment": claim.other_adjustment,
        "patient_responsibility": claim.patient_responsibility,
        "balance": claim.balance,
        "status": claim.status.value if claim.status else "pending",
        "denials": [
            {
                "carc_code": d.carc_code,
                "carc_description": d.carc_description,
                "denied_amount": d.denied_amount,
                "appeal_deadline": str(d.appeal_deadline) if d.appeal_deadline else None,
            }
            for d in claim.denials
        ],
    }

    service_lines = [
        {
            "procedure_code": s.procedure_code,
            "modifier_1": s.modifier_1,
            "modifier_2": s.modifier_2,
            "modifier_3": s.modifier_3,
            "modifier_4": s.modifier_4,
            "date_of_service_from": s.date_of_service_from,
            "units": s.units,
            "billed_amount": s.billed_amount,
            "allowed_amount": s.allowed_amount,
            "paid_amount": s.paid_amount,
            "patient_responsibility": s.patient_responsibility,
            "description": s.description,
        }
        for s in claim.service_lines
    ]

    adjustments = [
        {
            "group_code": a.group_code,
            "reason_code": a.reason_code,
            "amount": a.amount,
            "reason_description": a.reason_description,
        }
        for a in claim.adjustments
    ]

    os.makedirs(settings.export_dir, exist_ok=True)
    output_path = os.path.join(settings.export_dir, f"eob_{claim_id}.pdf")

    pdf_bytes = generate_eob_pdf(
        claim_data=claim_data,
        service_lines=service_lines,
        adjustments=adjustments,
        patient_info=patient_info,
        practice_info=practice_info,
        output_path=output_path,
    )

    log_action(db, "GENERATE_EOB", "claim", actor=current_user,
               resource_id=claim_id,
               patient_id=str(claim.patient_id) if claim.patient_id else None,
               description="EOB PDF generated")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=EOB_{claim.claim_number or claim_id}.pdf",
            "Content-Length": str(len(pdf_bytes)),
        },
    )
