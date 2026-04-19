"""
Fax endpoint — send documents via RingCentral fax API.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.document import PatientDocument
from app.models.patient_directory import IntakeDocument
from app.services.fax_service import send_fax, check_fax_status
from app.services.audit_service import log_action

router = APIRouter(prefix="/fax", tags=["fax"])


@router.post("/send")
def fax_document(
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    Send a document via fax.
    Body: { fax_number, doc_type ("document" or "intake"), doc_id, cover_text? }
    """
    fax_number = payload.get("fax_number", "").strip()
    doc_type = payload.get("doc_type", "document")
    doc_id = payload.get("doc_id", "")
    cover_text = payload.get("cover_text", "")

    if not fax_number:
        raise HTTPException(status_code=400, detail="fax_number is required")
    if not doc_id:
        raise HTTPException(status_code=400, detail="doc_id is required")

    # Find the file
    file_path = None
    patient_name = None

    if doc_type == "intake":
        doc = db.query(IntakeDocument).filter(IntakeDocument.id == doc_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Intake document not found")
        file_path = doc.file_path
        patient_name = doc.patient_name_raw
    else:
        doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        file_path = doc.file_path
        # Get patient name from directory
        from app.models.patient_directory import PatientDirectory
        patient = db.query(PatientDirectory).filter(
            PatientDirectory.chart_number == doc.chart_number
        ).first()
        patient_name = patient.patient_name if patient else doc.chart_number

    if not file_path:
        raise HTTPException(status_code=404, detail="File not available on disk")

    result = send_fax(
        to_number=fax_number,
        file_path=file_path,
        cover_page_text=cover_text,
        patient_name=patient_name,
    )

    if result.get("error"):
        log_action(db, "FAX_FAILED", "fax",
                   description=f"Fax failed to {fax_number}: {result['error']}")
        raise HTTPException(status_code=500, detail=result["error"])

    log_action(db, "FAX_SENT", "fax",
               description=f"Faxed {doc_type} to {fax_number} for {patient_name} — msg {result.get('message_id')}")

    return result


@router.get("/status/{message_id}")
def fax_status(message_id: str, db: Session = Depends(get_db)):
    """Check fax delivery status."""
    return check_fax_status(message_id)
