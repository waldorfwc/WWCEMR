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
from app.routers.auth import get_current_user, require_permission

router = APIRouter(prefix="/fax", tags=["fax"])


@router.post("/send")
def fax_document(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("fax:send")),
):
    """
    Legacy single-doc fax. Delegates to the new send-batch path so every
    send is tracked in fax_logs.
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

    if doc_type == "intake":
        # Intake docs aren't keyed by chart_number; keep legacy behavior — direct send,
        # no FaxLog row (FaxLog is scoped to PatientDocument-based chart flows).
        intake_doc = db.query(IntakeDocument).filter(IntakeDocument.id == doc_id).first()
        if not intake_doc:
            raise HTTPException(status_code=404, detail="Intake document not found")
        result = send_fax(
            to_number=fax_number, file_path=intake_doc.file_path,
            cover_page_text=cover_text,
            patient_name=intake_doc.patient_name_raw,
        )
        if result.get("error"):
            log_action(db, "FAX_FAILED", "fax",
                       user_name=current_user.get("email"),
                       description=f"Intake fax failed to {fax_number}: {result['error']}")
            raise HTTPException(status_code=500, detail=result["error"])
        log_action(db, "FAX_SENT", "fax",
                   user_name=current_user.get("email"),
                   description=f"Faxed intake to {fax_number} for {intake_doc.patient_name_raw} — msg {result.get('message_id')}")
        return result

    # Patient-doc path delegates to the batch endpoint.
    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    from app.routers.fax_batch import _send_batch_core, SendBatchPayload
    batch_result = _send_batch_core(
        SendBatchPayload(
            chart_number=doc.chart_number,
            doc_ids=[str(doc.id)],
            dest_fax=fax_number,
            grouping_mode="separate",
            cover_text=cover_text or None,
        ),
        db=db,
        sent_by=current_user.get("email"),
    )
    fax = batch_result["faxes"][0]
    if fax["status"] == "failed":
        raise HTTPException(status_code=500, detail=fax["error"])
    return {
        "success": True,
        "message_id": fax["ringcentral_message_id"],
        "status": "Sent",
        "to": fax_number,
        "pages": None,
        "error": None,
    }


@router.get("/status/{message_id}")
def fax_status(message_id: str, db: Session = Depends(get_db)):
    """Check fax delivery status."""
    return check_fax_status(message_id)
