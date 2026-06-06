"""
Patient document browser.
Indexes and serves PDFs extracted from PrimeSuite (Document.tar.*).
Directory structure: {documents_dir}/{chart_number}/{DocType}-{MMDDYYYY}-{docId}-{page}.pdf
"""

import os
import re
from datetime import date, datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse
from app.services.storage import serve_blob, using_gcs
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from app.database import get_db
from app.models.document import PatientDocument
from app.config import settings
from app.services.audit_service import log_action
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier

router = APIRouter(prefix="/documents", tags=["documents"])

# Filename pattern: DocType-MMDDYYYY-docId-page.pdf
_FNAME_RE = re.compile(
    r"^(?P<doc_type>.+?)-(?P<date>\d{8})-(?P<doc_id>\d+)-(?P<page>\d+)\.pdf$",
    re.IGNORECASE,
)


def _parse_filename(filename: str):
    """Parse a PrimeSuite document filename into components."""
    m = _FNAME_RE.match(filename)
    if not m:
        return None, None, None, 1
    doc_type = m.group("doc_type").strip()
    raw_date = m.group("date")
    doc_id = m.group("doc_id")
    page = int(m.group("page"))
    try:
        doc_date = date(int(raw_date[4:8]), int(raw_date[0:2]), int(raw_date[2:4]))
    except (ValueError, IndexError):
        doc_date = None
    return doc_type, doc_date, doc_id, page


@router.post("/index")
def index_documents(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: dict = Depends(requires_tier(Module.CHART, Tier.MANAGE)),
):
    """
    Scan the documents directory and index all PDFs into the database.
    Runs in the background — call /index/status to check progress.
    """
    docs_dir = settings.documents_dir
    if not os.path.isdir(docs_dir):
        raise HTTPException(status_code=404, detail=f"Documents directory not found: {docs_dir}")

    background_tasks.add_task(_do_index, docs_dir)
    return {"status": "indexing_started", "directory": docs_dir}


def _do_index(docs_dir: str):
    """Walk the directory tree and insert/update all documents."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        # Clear existing index and rebuild
        db.query(PatientDocument).delete()
        db.commit()

        batch = []
        batch_size = 500

        for chart_number in os.listdir(docs_dir):
            chart_dir = os.path.join(docs_dir, chart_number)
            if not os.path.isdir(chart_dir):
                continue
            for filename in os.listdir(chart_dir):
                if not filename.lower().endswith(".pdf"):
                    continue
                filepath = os.path.join(chart_dir, filename)
                doc_type, doc_date, doc_id, page = _parse_filename(filename)
                if doc_type is None:
                    doc_type = os.path.splitext(filename)[0]

                try:
                    size_kb = os.path.getsize(filepath) // 1024
                except OSError:
                    size_kb = 0

                batch.append(PatientDocument(
                    chart_number=chart_number,
                    doc_type=doc_type,
                    doc_date=doc_date,
                    doc_id=doc_id,
                    page_number=page,
                    filename=filename,
                    file_path=filepath,
                    file_size_kb=size_kb,
                ))

                if len(batch) >= batch_size:
                    db.bulk_save_objects(batch)
                    db.commit()
                    batch = []

        if batch:
            db.bulk_save_objects(batch)
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"[documents] Index error: {e}")
    finally:
        db.close()


@router.get("/index/status")
def index_status(db: Session = Depends(get_db)):
    """Return how many documents have been indexed."""
    count = db.query(func.count(PatientDocument.id)).scalar() or 0
    patients = db.query(func.count(distinct(PatientDocument.chart_number))).scalar() or 0
    doc_types = db.query(func.count(distinct(PatientDocument.doc_type))).scalar() or 0
    return {
        "indexed_documents": count,
        "indexed_patients": patients,
        "indexed_doc_types": doc_types,
        "documents_dir": settings.documents_dir,
        "dir_exists": os.path.isdir(settings.documents_dir),
    }


@router.get("")
def list_documents(
    chart_number: Optional[str] = None,
    doc_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    """List documents with filters."""
    q = db.query(PatientDocument).filter(PatientDocument.file_path != "")
    if chart_number:
        q = q.filter(PatientDocument.chart_number == chart_number)
    if doc_type:
        q = q.filter(PatientDocument.doc_type.ilike(f"%{doc_type}%"))
    if date_from:
        try:
            q = q.filter(PatientDocument.doc_date >= date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(PatientDocument.doc_date <= date.fromisoformat(date_to))
        except ValueError:
            pass
    if search:
        q = q.filter(PatientDocument.doc_type.ilike(f"%{search}%"))

    total = q.count()
    docs = (
        q.order_by(PatientDocument.chart_number, PatientDocument.doc_date.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "documents": [_doc_to_dict(d) for d in docs],
    }


@router.get("/patient/{chart_number}")
def patient_documents(
    chart_number: str,
    doc_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """All documents for a specific patient chart number."""
    q = db.query(PatientDocument).filter(PatientDocument.chart_number == chart_number)
    if doc_type:
        q = q.filter(PatientDocument.doc_type.ilike(f"%{doc_type}%"))

    docs = q.order_by(PatientDocument.doc_date.desc()).all()
    if not docs:
        raise HTTPException(status_code=404, detail=f"No documents found for chart {chart_number}")

    # Group by doc_type for sidebar display
    by_type: dict = {}
    for d in docs:
        by_type.setdefault(d.doc_type, []).append(_doc_to_dict(d))

    log_action(db, "VIEW", "patient_documents", description=f"Viewed documents for chart {chart_number}")

    return {
        "chart_number": chart_number,
        "total": len(docs),
        "by_type": by_type,
        "documents": [_doc_to_dict(d) for d in docs],
    }


@router.get("/types")
def list_doc_types(db: Session = Depends(get_db)):
    """All distinct document types with counts."""
    rows = (
        db.query(PatientDocument.doc_type, func.count(PatientDocument.id).label("count"))
        .group_by(PatientDocument.doc_type)
        .order_by(func.count(PatientDocument.id).desc())
        .all()
    )
    return {"types": [{"type": r.doc_type, "count": r.count} for r in rows]}


@router.get("/patients")
def list_patients(
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(100, le=500),
    db: Session = Depends(get_db),
):
    """All patient chart numbers with document counts and patient name."""
    from app.models.patient_directory import PatientDirectory

    q = db.query(
        PatientDocument.chart_number,
        func.count(PatientDocument.id).label("count"),
        func.min(PatientDocument.doc_date).label("earliest"),
        func.max(PatientDocument.doc_date).label("latest"),
    ).filter(PatientDocument.file_path != "").group_by(PatientDocument.chart_number)

    if search:
        from sqlalchemy import cast, String
        # Search matches patient_name, chart_number, or DOB (substring).
        # DOB stored as Date — cast to string for substring matching.
        matching_charts = db.query(PatientDirectory.chart_number).filter(
            PatientDirectory.patient_name.ilike(f"%{search}%")
            | cast(PatientDirectory.dob, String).ilike(f"%{search}%")
        ).subquery()
        q = q.filter(
            PatientDocument.chart_number.ilike(f"%{search}%")
            | PatientDocument.chart_number.in_(matching_charts)
        )

    total = q.count()
    rows = q.order_by(PatientDocument.chart_number)\
            .offset((page - 1) * per_page).limit(per_page).all()

    # Fetch patient names for these charts
    chart_nums = [r.chart_number for r in rows]
    name_map = {}
    if chart_nums:
        dir_rows = db.query(PatientDirectory).filter(
            PatientDirectory.chart_number.in_(chart_nums)
        ).all()
        name_map = {d.chart_number: (d.patient_name, str(d.dob) if d.dob else None) for d in dir_rows}

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "patients": [
            {
                "chart_number": r.chart_number,
                "patient_name": name_map.get(r.chart_number, (None, None))[0],
                "dob": name_map.get(r.chart_number, (None, None))[1],
                "document_count": r.count,
                "earliest_doc": str(r.earliest) if r.earliest else None,
                "latest_doc": str(r.latest) if r.latest else None,
            }
            for r in rows
        ],
    }


def _resolve_file(doc: PatientDocument, db: Session) -> str:
    """
    Return a valid local file path for the document.
    If not extracted yet, attempt to extract from the tar archive on the external drive.
    """
    if doc.file_path and os.path.isfile(doc.file_path):
        return doc.file_path

    # Try to extract from external drive tar files on demand
    ext_docs = "/Volumes/OWC External/Documents"
    extracted_base = os.path.expanduser("~/Downloads/wwc_documents/Document")
    tar_member = f"Document/{doc.chart_number}/{doc.filename}"
    local_dest = os.path.join(extracted_base, doc.chart_number)

    if os.path.isdir(ext_docs):
        import subprocess
        os.makedirs(local_dest, exist_ok=True)
        # Search each tar part for the file
        tar_parts = sorted(
            [os.path.join(ext_docs, f) for f in os.listdir(ext_docs) if f.startswith("Document.tar-")]
        )
        for part in tar_parts:
            try:
                result = subprocess.run(
                    ["tar", "xf", part, "-C", os.path.dirname(extracted_base), tar_member],
                    capture_output=True, timeout=30,
                )
                candidate = os.path.join(extracted_base, doc.chart_number, doc.filename)
                if os.path.isfile(candidate):
                    doc.file_path = candidate
                    db.commit()
                    return candidate
            except Exception:
                continue

    return ""


@router.get("/download/{doc_id}")
def download_document(doc_id: str, db: Session = Depends(get_db)):
    """Download a specific document by its database ID."""
    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # GCS mode: skip local resolve, go straight to bucket
    if using_gcs():
        log_action(
            db, "DOWNLOAD", "patient_document",
            resource_id=doc_id,
            description=f"Downloaded {doc.doc_type} for chart {doc.chart_number}",
        )
        return serve_blob(
            local_path=None,
            gcs_object=f"extracted/Document/{doc.chart_number}/{doc.filename}",
            media_type="application/pdf",
            filename=doc.filename,
            disposition="attachment",
        )

    path = _resolve_file(doc, db)
    if not path:
        raise HTTPException(
            status_code=404,
            detail="File not locally available. Connect the OWC External drive to extract on demand."
        )

    log_action(
        db, "DOWNLOAD", "patient_document",
        resource_id=doc_id,
        description=f"Downloaded {doc.doc_type} for chart {doc.chart_number}",
    )
    return FileResponse(
        path=path,
        media_type="application/pdf",
        filename=doc.filename,
        headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'},
    )


@router.get("/view/{doc_id}")
def view_document(doc_id: str, db: Session = Depends(get_db)):
    """Inline-view a PDF in the browser."""
    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if using_gcs():
        log_action(
            db, "VIEW", "patient_document",
            resource_id=doc_id,
            description=f"Viewed {doc.doc_type} for chart {doc.chart_number}",
        )
        return serve_blob(
            local_path=None,
            gcs_object=f"extracted/Document/{doc.chart_number}/{doc.filename}",
            media_type="application/pdf",
            filename=doc.filename,
            disposition="inline",
        )

    path = _resolve_file(doc, db)
    if not path:
        raise HTTPException(
            status_code=404,
            detail="File not locally available. Connect the OWC External drive to extract on demand."
        )

    log_action(
        db, "VIEW", "patient_document",
        resource_id=doc_id,
        description=f"Viewed {doc.doc_type} for chart {doc.chart_number}",
    )
    return FileResponse(
        path=path,
        media_type="application/pdf",
        filename=doc.filename,
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )


@router.get("/summary")
def documents_summary(db: Session = Depends(get_db)):
    """High-level summary counts for the dashboard."""
    total = db.query(func.count(PatientDocument.id)).scalar() or 0
    patients = db.query(func.count(distinct(PatientDocument.chart_number))).scalar() or 0

    # Insurance cards (all variants)
    insurance_cards = db.query(func.count(PatientDocument.id)).filter(
        PatientDocument.doc_type.ilike("%insurance card%")
    ).scalar() or 0

    # Driver's licenses
    drivers_licenses = db.query(func.count(PatientDocument.id)).filter(
        PatientDocument.doc_type.ilike("%driver%")
    ).scalar() or 0

    return {
        "total_documents": total,
        "total_patients": patients,
        "insurance_cards": insurance_cards,
        "drivers_licenses": drivers_licenses,
    }


def _doc_to_dict(d: PatientDocument) -> dict:
    return {
        "id": str(d.id),
        "chart_number": d.chart_number,
        "doc_type": d.doc_type,
        "doc_date": str(d.doc_date) if d.doc_date else None,
        "doc_id": d.doc_id,
        "page_number": d.page_number,
        "filename": d.filename,
        "file_size_kb": d.file_size_kb,
        "available": bool(d.file_path and os.path.isfile(d.file_path)),
    }
