"""Fax batch router — send-batch is the core entry; separate mode only in this task.

Combined and by_type modes are added in later tasks.
"""
import os
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document import PatientDocument
from app.models.patient_directory import PatientDirectory
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.services.fax_service import send_fax
from app.services.pdf_merge import merge_pdfs
from app.services.audit_service import log_action

router = APIRouter(prefix="/fax", tags=["fax-batch"])


class SendBatchPayload(BaseModel):
    chart_number: str
    doc_ids: list[str]
    dest_fax: str
    grouping_mode: str = "separate"
    cover_text: Optional[str] = None


def _patient_name(db: Session, chart_number: str) -> str:
    p = db.query(PatientDirectory).filter(PatientDirectory.chart_number == chart_number).first()
    return p.patient_name if p else chart_number


def _send_one_and_log(
    db: Session,
    chart_number: str,
    dest_fax: str,
    doc_ids: list[str],
    file_path: Optional[str],
    cover_text: Optional[str],
    patient_name: str,
    grouping_mode: str,
    not_found_error: Optional[str] = None,
) -> dict:
    """Create FaxLog row, call RingCentral (unless pre-failed), return payload row dict."""
    log = FaxLog(
        chart_number=chart_number,
        doc_ids=doc_ids,
        grouping_mode=grouping_mode,
        dest_fax=dest_fax,
    )
    db.add(log)
    db.flush()

    if not_found_error:
        log.status = FaxLogStatus.FAILED
        log.error = not_found_error
        db.commit()
        log_action(db, "FAX_FAILED", "fax", resource_id=str(log.id),
                   description=f"Fax failed: {not_found_error}")
        return {"fax_log_id": str(log.id), "doc_ids": doc_ids,
                "status": "failed", "error": not_found_error,
                "ringcentral_message_id": None}

    result = send_fax(
        to_number=dest_fax, file_path=file_path,
        cover_page_text=cover_text, patient_name=patient_name,
    )
    if result.get("error"):
        log.status = FaxLogStatus.FAILED
        log.error = result["error"]
        db.commit()
        log_action(db, "FAX_FAILED", "fax", resource_id=str(log.id),
                   description=f"Fax to {dest_fax} failed: {result['error']}")
        return {"fax_log_id": str(log.id), "doc_ids": doc_ids,
                "status": "failed", "error": result["error"],
                "ringcentral_message_id": None}

    log.status = FaxLogStatus.SENT
    log.ringcentral_message_id = result.get("message_id")
    log.sent_at = datetime.utcnow()
    db.commit()
    log_action(db, "FAX_SENT", "fax", resource_id=str(log.id),
               description=f"Faxed {len(doc_ids)} doc(s) to {dest_fax} — msg {result.get('message_id')}")
    return {"fax_log_id": str(log.id), "doc_ids": doc_ids,
            "status": "sent", "error": None,
            "ringcentral_message_id": result.get("message_id")}


@router.post("/send-batch")
def send_batch(payload: SendBatchPayload, db: Session = Depends(get_db)):
    if not payload.doc_ids:
        raise HTTPException(status_code=400, detail="doc_ids must not be empty")
    if payload.grouping_mode not in {m.value for m in GroupingMode}:
        raise HTTPException(status_code=400, detail=f"Invalid grouping_mode: {payload.grouping_mode}")

    patient_name = _patient_name(db, payload.chart_number)
    mode = payload.grouping_mode

    log_action(db, "FAX_BATCH_SENT", "fax",
               description=f"Batch fax chart={payload.chart_number} docs={len(payload.doc_ids)} mode={mode} to {payload.dest_fax}")

    faxes = []
    if mode == "separate":
        for doc_id in payload.doc_ids:
            doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
            if not doc:
                faxes.append(_send_one_and_log(
                    db, payload.chart_number, payload.dest_fax, [doc_id],
                    file_path=None, cover_text=payload.cover_text,
                    patient_name=patient_name, grouping_mode=mode,
                    not_found_error=f"Document {doc_id} not found",
                ))
                continue
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax, [doc_id],
                file_path=doc.file_path, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
            ))
    elif mode == "combined":
        # Validate every doc exists first.
        docs = []
        missing = []
        for doc_id in payload.doc_ids:
            doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
            if not doc:
                missing.append(doc_id)
            else:
                docs.append(doc)

        if missing:
            # Record one failed batch and return
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax, list(payload.doc_ids),
                file_path=None, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
                not_found_error=f"Documents not found: {', '.join(missing)}",
            ))
            return {"batch_id": None, "faxes": faxes}

        merged_path = None
        try:
            merged_path = merge_pdfs([d.file_path for d in docs])
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax,
                [str(d.id) for d in docs],
                file_path=merged_path, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
            ))
        except (FileNotFoundError, ValueError) as e:
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax,
                [str(d.id) for d in docs],
                file_path=None, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
                not_found_error=f"PDF merge failed: {e}",
            ))
        finally:
            if merged_path and os.path.isfile(merged_path):
                os.unlink(merged_path)
    elif mode == "by_type":
        # Group loaded docs by their doc_type, merge each group, send one fax per group.
        loaded = []
        missing = []
        for doc_id in payload.doc_ids:
            doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
            if doc is None:
                missing.append(doc_id)
            else:
                loaded.append(doc)

        if missing:
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax, list(payload.doc_ids),
                file_path=None, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
                not_found_error=f"Documents not found: {', '.join(missing)}",
            ))
            return {"batch_id": None, "faxes": faxes}

        groups: dict[str, list[PatientDocument]] = {}
        for doc in loaded:
            groups.setdefault(doc.doc_type, []).append(doc)

        for doc_type, group in groups.items():
            merged_path = None
            try:
                if len(group) == 1:
                    # No merge needed; send the single file directly
                    faxes.append(_send_one_and_log(
                        db, payload.chart_number, payload.dest_fax,
                        [str(group[0].id)],
                        file_path=group[0].file_path, cover_text=payload.cover_text,
                        patient_name=patient_name, grouping_mode=mode,
                    ))
                    continue

                merged_path = merge_pdfs([d.file_path for d in group])
                faxes.append(_send_one_and_log(
                    db, payload.chart_number, payload.dest_fax,
                    [str(d.id) for d in group],
                    file_path=merged_path, cover_text=payload.cover_text,
                    patient_name=patient_name, grouping_mode=mode,
                ))
            except (FileNotFoundError, ValueError) as e:
                faxes.append(_send_one_and_log(
                    db, payload.chart_number, payload.dest_fax,
                    [str(d.id) for d in group],
                    file_path=None, cover_text=payload.cover_text,
                    patient_name=patient_name, grouping_mode=mode,
                    not_found_error=f"PDF merge failed for doc_type={doc_type}: {e}",
                ))
            finally:
                if merged_path and os.path.isfile(merged_path):
                    os.unlink(merged_path)

    return {"batch_id": None, "faxes": faxes}


@router.get("/recent")
def fax_recent(limit: int = 5, db: Session = Depends(get_db)):
    """Recent fax activity for the Dashboard card."""
    rows = (
        db.query(FaxLog)
        .order_by(FaxLog.sent_at.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    if not rows:
        return []

    charts = {r.chart_number for r in rows}
    patients = {
        p.chart_number: p.patient_name
        for p in db.query(PatientDirectory)
        .filter(PatientDirectory.chart_number.in_(charts))
        .all()
    }

    def row_to_dict(r: FaxLog) -> dict:
        return {
            "id": str(r.id),
            "chart_number": r.chart_number,
            "patient_name": patients.get(r.chart_number, r.chart_number),
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "sent_at": r.sent_at.isoformat() + "Z" if r.sent_at else None,
            "dest_fax": r.dest_fax,
            "doc_count": len(r.doc_ids or []),
        }

    return [row_to_dict(r) for r in rows]


@router.get("/by-chart/{chart_number}")
def fax_by_chart(chart_number: str, db: Session = Depends(get_db)):
    """Every fax attempt for a single chart, newest first. Used by the chart-view chips."""
    rows = (
        db.query(FaxLog)
        .filter(FaxLog.chart_number == chart_number)
        .order_by(FaxLog.sent_at.desc())
        .all()
    )
    return [{
        "id": str(r.id),
        "chart_number": r.chart_number,
        "doc_ids": r.doc_ids or [],
        "grouping_mode": r.grouping_mode.value if hasattr(r.grouping_mode, "value") else r.grouping_mode,
        "dest_fax": r.dest_fax,
        "status": r.status.value if hasattr(r.status, "value") else r.status,
        "sent_at": r.sent_at.isoformat() + "Z" if r.sent_at else None,
        "delivered_at": r.delivered_at.isoformat() + "Z" if r.delivered_at else None,
        "error": r.error,
        "ringcentral_message_id": r.ringcentral_message_id,
    } for r in rows]


@router.post("/retry/{fax_log_id}")
def fax_retry(fax_log_id: str, db: Session = Depends(get_db)):
    """Resend a fax with the same doc_ids / dest / grouping as the original.
    Creates a new FaxLog row that points back to the original via retry_of.
    """
    original = db.query(FaxLog).filter(FaxLog.id == fax_log_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Fax log not found")

    mode = original.grouping_mode.value if hasattr(original.grouping_mode, "value") else original.grouping_mode
    batch = send_batch(
        SendBatchPayload(
            chart_number=original.chart_number,
            doc_ids=list(original.doc_ids or []),
            dest_fax=original.dest_fax,
            grouping_mode=mode,
            cover_text=None,  # cover text isn't persisted; retry regenerates
        ),
        db=db,
    )
    # Link every new FaxLog in the batch to the original
    for fax in batch["faxes"]:
        new_id = fax.get("fax_log_id")
        if new_id:
            new_log = db.query(FaxLog).filter(FaxLog.id == new_id).first()
            if new_log:
                new_log.retry_of = original.id
    db.commit()
    return batch
