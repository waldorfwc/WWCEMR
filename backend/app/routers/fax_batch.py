"""Fax batch router — send-batch is the core entry; separate mode only in this task.

Combined and by_type modes are added in later tasks.
"""
import os
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document import PatientDocument
from app.models.patient_directory import PatientDirectory
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.services.fax_service import send_fax
from app.services.pdf_merge import merge_pdfs
from app.services.audit_service import log_action
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier

router = APIRouter(prefix="/fax", tags=["fax-batch"])
log_router = APIRouter(prefix="/fax-log", tags=["fax-log"])


class SendBatchPayload(BaseModel):
    chart_number: str
    doc_ids: list[str]
    dest_fax: str
    grouping_mode: str = "separate"
    cover_text: Optional[str] = None
    # Idempotency key. If supplied and a FaxLog row already exists for
    # (chart_number, client_request_id), the endpoint returns the prior
    # batch instead of re-faxing. Frontend should generate a UUID once
    # per Send-click. (Fable recalls audit C3.)
    client_request_id: Optional[str] = None

    @field_validator("dest_fax")
    @classmethod
    def _validate_fax(cls, v: str) -> str:
        """Reject anything that doesn't normalize to +1NXXNXXXXXX before
        any FaxLog row is created. Otherwise a typo (9- or 12-digit
        garbage) reached send_fax which only logged an error after the
        log row was persisted. (Fable recalls audit H2.)"""
        import re as _re
        clean = (v or "").strip().replace("-", "").replace("(", "").replace(")", "").replace(" ", "").replace(".", "")
        if not clean.startswith("+"):
            if len(clean) == 10:
                clean = "+1" + clean
            elif len(clean) == 11 and clean.startswith("1"):
                clean = "+" + clean
        if not _re.fullmatch(r"\+1\d{10}", clean):
            raise ValueError(
                f"dest_fax must be a US fax number (10 or 11 digits); got {v!r}")
        return v


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
    sent_by: Optional[str] = None,
    not_found_error: Optional[str] = None,
    client_request_id: Optional[str] = None,
) -> dict:
    """Create FaxLog row, call RingCentral (unless pre-failed), return payload row dict."""
    log = FaxLog(
        chart_number=chart_number,
        doc_ids=doc_ids,
        grouping_mode=grouping_mode,
        dest_fax=dest_fax,
        sent_by=sent_by,
        cover_text=cover_text,
        client_request_id=client_request_id,
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
def send_batch(
    payload: SendBatchPayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    return _send_batch_core(payload, db, sent_by=current_user.get("email"))


def _send_batch_core(
    payload: SendBatchPayload,
    db: Session,
    sent_by: Optional[str] = None,
):
    """Core implementation of fax/send-batch — no FastAPI dependencies.

    Callable from any router. Writes one FaxLog row per fax attempt
    (one for 'separate' and 'by_type', one for 'combined'). Each row
    is committed individually inside `_send_one_and_log` so a mid-batch
    failure leaves prior rows persisted. `sent_by` populates FaxLog.sent_by
    on every new row.

    Returns:
        {"batch_id": None, "faxes": [{"fax_log_id", "doc_ids", "status",
            "error", "ringcentral_message_id"}]}
    """
    if not payload.doc_ids:
        raise HTTPException(status_code=400, detail="doc_ids must not be empty")
    if payload.grouping_mode not in {m.value for m in GroupingMode}:
        raise HTTPException(status_code=400, detail=f"Invalid grouping_mode: {payload.grouping_mode}")

    # Idempotency short-circuit: if the client supplied a request id and a
    # FaxLog already exists for this (chart, client_request_id), return the
    # prior batch unchanged. A double-clicked Send button (or retried HTTP
    # call) hits this path and we don't re-fax. (Fable recalls audit C3.)
    if payload.client_request_id:
        prior = (db.query(FaxLog)
                    .filter(FaxLog.chart_number == payload.chart_number,
                            FaxLog.client_request_id == payload.client_request_id)
                    .order_by(FaxLog.created_at.asc())
                    .all())
        if prior:
            return {
                "batch_id": None,
                "faxes": [{
                    "fax_log_id": str(r.id),
                    "doc_ids": r.doc_ids or [],
                    "status": r.status.value if hasattr(r.status, "value") else r.status,
                    "error": r.error,
                    "ringcentral_message_id": r.ringcentral_message_id,
                } for r in prior],
                "idempotent_replay": True,
            }

    patient_name = _patient_name(db, payload.chart_number)
    mode = payload.grouping_mode

    log_action(db, "FAX_BATCH_SENT", "fax",
               user_name=sent_by,
               description=f"Batch fax chart={payload.chart_number} docs={len(payload.doc_ids)} mode={mode} to {payload.dest_fax}")

    faxes = []
    # Wrong-patient guard: a doc id that resolves to a different chart
    # is treated as not-found. Without this, a fat-fingered chart_number
    # would fax patient A's PHI while the FaxLog/cover-page name patient B.
    # (Fable C2.)
    def _owns_chart(doc) -> bool:
        return bool(doc) and doc.chart_number == payload.chart_number
    if mode == "separate":
        for doc_id in payload.doc_ids:
            doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
            if not _owns_chart(doc):
                faxes.append(_send_one_and_log(
                    db, payload.chart_number, payload.dest_fax, [doc_id],
                    file_path=None, cover_text=payload.cover_text,
                    patient_name=patient_name, grouping_mode=mode,
                    sent_by=sent_by,
                    client_request_id=payload.client_request_id,
                    not_found_error=f"Document {doc_id} not found",
                ))
                continue
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax, [doc_id],
                file_path=doc.file_path, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
                sent_by=sent_by,
                client_request_id=payload.client_request_id,
            ))
    elif mode == "combined":
        # Validate every doc exists AND belongs to the requested chart.
        # (Fable C2.)
        docs = []
        missing = []
        for doc_id in payload.doc_ids:
            doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
            if not _owns_chart(doc):
                missing.append(doc_id)
            else:
                docs.append(doc)

        if missing:
            # Record one failed batch and return
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax, list(payload.doc_ids),
                file_path=None, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
                sent_by=sent_by,
                client_request_id=payload.client_request_id,
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
                sent_by=sent_by,
                client_request_id=payload.client_request_id,
            ))
        except (FileNotFoundError, ValueError) as e:
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax,
                [str(d.id) for d in docs],
                file_path=None, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
                sent_by=sent_by,
                client_request_id=payload.client_request_id,
                not_found_error=f"PDF merge failed: {e}",
            ))
        finally:
            if merged_path and os.path.isfile(merged_path):
                os.unlink(merged_path)
    elif mode == "by_type":
        # Group loaded docs by their doc_type, merge each group, send one fax per group.
        # Same wrong-chart guard as the other modes. (Fable C2.)
        loaded = []
        missing = []
        for doc_id in payload.doc_ids:
            doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
            if not _owns_chart(doc):
                missing.append(doc_id)
            else:
                loaded.append(doc)

        if missing:
            faxes.append(_send_one_and_log(
                db, payload.chart_number, payload.dest_fax, list(payload.doc_ids),
                file_path=None, cover_text=payload.cover_text,
                patient_name=patient_name, grouping_mode=mode,
                sent_by=sent_by,
                client_request_id=payload.client_request_id,
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
                        sent_by=sent_by,
                        client_request_id=payload.client_request_id,
                    ))
                    continue

                merged_path = merge_pdfs([d.file_path for d in group])
                faxes.append(_send_one_and_log(
                    db, payload.chart_number, payload.dest_fax,
                    [str(d.id) for d in group],
                    file_path=merged_path, cover_text=payload.cover_text,
                    patient_name=patient_name, grouping_mode=mode,
                    sent_by=sent_by,
                    client_request_id=payload.client_request_id,
                ))
            except (FileNotFoundError, ValueError) as e:
                faxes.append(_send_one_and_log(
                    db, payload.chart_number, payload.dest_fax,
                    [str(d.id) for d in group],
                    file_path=None, cover_text=payload.cover_text,
                    patient_name=patient_name, grouping_mode=mode,
                    sent_by=sent_by,
                    client_request_id=payload.client_request_id,
                    not_found_error=f"PDF merge failed for doc_type={doc_type}: {e}",
                ))
            finally:
                if merged_path and os.path.isfile(merged_path):
                    os.unlink(merged_path)

    return {"batch_id": None, "faxes": faxes}


@router.get("/recent")
def fax_recent(
    limit: int = 5,
    window: Optional[int] = None,  # days; None = no window
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    _perm: dict = Depends(requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    """Recent fax activity for Dashboard card AND Charts-page fax-log pane."""
    q = db.query(FaxLog)
    if window:
        from datetime import datetime, timedelta
        q = q.filter(FaxLog.sent_at >= datetime.utcnow() - timedelta(days=window))
    if status:
        q = q.filter(FaxLog.status == status)

    rows = q.order_by(FaxLog.sent_at.desc()).limit(max(1, min(limit, 200))).all()
    if not rows:
        return []

    # Bulk patient lookup
    charts = {r.chart_number for r in rows}
    dir_rows = db.query(PatientDirectory).filter(PatientDirectory.chart_number.in_(charts)).all()
    patient_map = {p.chart_number: p for p in dir_rows}

    # Bulk doc_type lookup — gather every doc_id across all rows, one query
    all_doc_ids = {d for r in rows for d in (r.doc_ids or [])}
    from app.models.document import PatientDocument
    doc_types_by_id: dict[str, str] = {}
    if all_doc_ids:
        doc_rows = db.query(PatientDocument.id, PatientDocument.doc_type).filter(
            PatientDocument.id.in_(all_doc_ids)
        ).all()
        doc_types_by_id = {str(d.id): d.doc_type for d in doc_rows}

    def serialize(r: FaxLog) -> dict:
        p = patient_map.get(r.chart_number)
        types = sorted({doc_types_by_id[d] for d in (r.doc_ids or []) if d in doc_types_by_id})
        return {
            "id": str(r.id),
            "chart_number": r.chart_number,
            "patient_name": p.patient_name if p else r.chart_number,
            "dob": str(p.dob) if p and p.dob else None,
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "sent_at": r.sent_at.isoformat() + "Z" if r.sent_at else None,
            "dest_fax": r.dest_fax,
            "doc_count": len(r.doc_ids or []),
            "doc_types": types,
            "sent_by": r.sent_by,
        }

    return [serialize(r) for r in rows]


@router.get("/by-chart/{chart_number}")
def fax_by_chart(
    chart_number: str,
    db: Session = Depends(get_db),
    _perm: dict = Depends(requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
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
def fax_retry(
    fax_log_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    """Resend a fax with the same doc_ids / dest / grouping as the original.
    Creates a new FaxLog row that points back to the original via retry_of.

    Refuses to retry an already-SENT fax (and an already-DELIVERED fax)
    unless ?force=true is passed — protects against accidental re-fax of
    a successfully transmitted document. Also blocks rapid double-retries
    of the same original (in-flight retry within the last 60 seconds).
    (Fable recalls audit C3.)
    """
    original = db.query(FaxLog).filter(FaxLog.id == fax_log_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Fax log not found")
    status_val = original.status.value if hasattr(original.status, "value") else original.status
    if not force and status_val in ("sent", "delivered"):
        raise HTTPException(
            status_code=409,
            detail=(f"Fax already {status_val} — pass ?force=true to "
                    "re-send anyway. Most often this means the previous "
                    "delivery worked and a retry would re-transmit PHI."))
    # In-flight retry guard. If anyone retried this original in the last
    # 60s, refuse — covers the double-click case where the user clicks
    # Retry twice before the first one finishes.
    from datetime import timedelta
    recent_retry = (db.query(FaxLog)
                       .filter(FaxLog.retry_of == original.id,
                               FaxLog.created_at
                                   >= datetime.utcnow() - timedelta(seconds=60))
                       .first())
    if recent_retry and not force:
        raise HTTPException(
            status_code=409,
            detail=(f"A retry is already in flight (fax_log {recent_retry.id}). "
                    "Wait for it to finish or pass ?force=true."))
    mode = original.grouping_mode.value if hasattr(original.grouping_mode, "value") else original.grouping_mode
    batch = _send_batch_core(
        SendBatchPayload(
            chart_number=original.chart_number,
            doc_ids=list(original.doc_ids or []),
            dest_fax=original.dest_fax,
            grouping_mode=mode,
            cover_text=original.cover_text,
        ),
        db=db,
        sent_by=current_user.get("email"),
    )
    for fax in batch["faxes"]:
        new_id = fax.get("fax_log_id")
        if new_id:
            new_log = db.query(FaxLog).filter(FaxLog.id == new_id).first()
            if new_log:
                new_log.retry_of = original.id
    db.commit()
    return batch


@log_router.get("")
def fax_log_list(
    status: Optional[str] = None,
    chart: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _perm: dict = Depends(requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    page = max(1, page)
    page_size = max(1, min(page_size, 200))

    q = db.query(FaxLog)
    if status:
        q = q.filter(FaxLog.status == status)
    if chart:
        q = q.filter(FaxLog.chart_number == chart)
    if date_from:
        q = q.filter(FaxLog.sent_at >= date_from)
    if date_to:
        q = q.filter(FaxLog.sent_at <= date_to)

    total = q.count()
    rows = (
        q.order_by(FaxLog.sent_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    charts = {r.chart_number for r in rows}
    patients = {
        p.chart_number: p.patient_name
        for p in db.query(PatientDirectory)
        .filter(PatientDirectory.chart_number.in_(charts))
        .all()
    } if charts else {}

    def serialize(r: FaxLog) -> dict:
        return {
            "id": str(r.id),
            "chart_number": r.chart_number,
            "patient_name": patients.get(r.chart_number, r.chart_number),
            "doc_count": len(r.doc_ids or []),
            "grouping_mode": r.grouping_mode.value if hasattr(r.grouping_mode, "value") else r.grouping_mode,
            "dest_fax": r.dest_fax,
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "sent_at": r.sent_at.isoformat() + "Z" if r.sent_at else None,
            "delivered_at": r.delivered_at.isoformat() + "Z" if r.delivered_at else None,
            "error": r.error,
        }

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": [serialize(r) for r in rows],
    }


@router.get("/chart-summary")
def fax_chart_summary(
    db: Session = Depends(get_db),
    _perm: dict = Depends(requires_tier(Module.ACTIVE_AR, Tier.WORK)),
):
    """Per-chart fax aggregates for the patient-list fax indicator.
    Returns one row per chart_number that has any FaxLog activity."""
    from sqlalchemy import func as sql_func
    rows = (
        db.query(
            FaxLog.chart_number,
            sql_func.count(FaxLog.id).label("fax_count"),
            sql_func.max(FaxLog.sent_at).label("last_sent_at"),
        )
        .group_by(FaxLog.chart_number)
        .all()
    )
    return [
        {
            "chart_number": r.chart_number,
            "fax_count": int(r.fax_count),
            "last_sent_at": r.last_sent_at.isoformat() + "Z" if r.last_sent_at else None,
        }
        for r in rows
    ]
