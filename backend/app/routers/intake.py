"""
Intake document management:
- Build patient directory from Phreesia PDFs
- Index intake archive files (organized by name+DOB)
- Match intake documents to chart numbers
- Browse, download, override matches
"""

import logging
import os
from typing import Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse
from app.services.storage import serve_blob, using_gcs

_INTAKE_LOCAL_ROOT = "/Volumes/OWC External/IntakeArchive/"
_INTAKE_GCS_BUCKET = os.environ.get("DOCUMENTS_GCS_BUCKET", "wwc-app-docs")


# Module-level cache for archive lookups. Only successful, non-empty
# results land here — a transient GCS error must not poison the year
# forever. The old lru_cache decorator cached () on failure, which meant
# every download for that DOB-year 404'd until the service restarted.
# (Fable intake audit #5.)
_archives_cache: dict[str, Tuple[str, ...]] = {}


def _intake_archives_for_year(year: str) -> Tuple[str, ...]:
    """Return the archive folder names (e.g. '1975-20260417T074958Z-3-001')
    matching a DOB-year prefix. One GCS list call per year on first hit.

    On GCS error returns () but does NOT cache, so the next call retries.
    On empty (legitimate "no archives for this year") returns () and
    doesn't cache either — a year going from empty → populated is rare
    but worth picking up without a restart.
    """
    if year in _archives_cache:
        return _archives_cache[year]
    try:
        from google.cloud import storage  # type: ignore
        client = storage.Client()
        bucket = client.bucket(_INTAKE_GCS_BUCKET)
        prefix = f"intake/{year}-"
        iterator = client.list_blobs(bucket, prefix=prefix, delimiter="/")
        list(iterator)  # populate iterator.prefixes
        out = tuple(sorted(p[len("intake/"):].rstrip("/") for p in iterator.prefixes))
        if out:
            _archives_cache[year] = out
        return out
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to list intake archives for year=%s", year)
        return ()


def _intake_gcs_object(file_path: str) -> str:
    """Map a local intake file path to its GCS object key.

    Three path shapes are supported:
      1. Mac Mini Downloads (current): `…/wwc_intake_docs/YYYY/MM/DD/…`
         → resolved to `intake/{archive}/YYYY/MM/DD/…` where {archive}
         is found by listing archives whose name starts with the file's
         DOB year. If multiple archives share a year, we probe each to
         find the one containing this file.
      2. Legacy external drive: `/Volumes/OWC External/IntakeArchive/…`
         → `intake/…`
      3. Anything containing `IntakeArchive/` → strip prefix.
    """
    if not file_path:
        return ""

    # Shape 1: wwc_intake_docs/YYYY/MM/DD/…
    marker = "wwc_intake_docs/"
    idx = file_path.find(marker)
    if idx >= 0:
        relative = file_path[idx + len(marker):]
        parts = relative.split("/", 1)
        if len(parts) >= 1 and parts[0].isdigit() and len(parts[0]) == 4:
            year = parts[0]
            archives = _intake_archives_for_year(year)
            if not archives:
                return ""
            if len(archives) == 1:
                return f"intake/{archives[0]}/{relative}"
            # Multiple archives for the same year — probe each.
            try:
                from google.cloud import storage  # type: ignore
                client = storage.Client()
                bucket = client.bucket(_INTAKE_GCS_BUCKET)
                for archive in archives:
                    candidate = f"intake/{archive}/{relative}"
                    if bucket.blob(candidate).exists():
                        return candidate
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed probing intake archives for %s", file_path)
            return ""
        # No year segment — last-resort try direct under intake/
        return f"intake/{relative}"

    # Shape 2 + 3: legacy external drive paths
    if file_path.startswith(_INTAKE_LOCAL_ROOT):
        return f"intake/{file_path[len(_INTAKE_LOCAL_ROOT):]}"
    idx = file_path.find("IntakeArchive/")
    if idx >= 0:
        return f"intake/{file_path[idx + len('IntakeArchive/'):]}"
    return ""
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from app.database import get_db, SessionLocal
from app.models.patient_directory import PatientDirectory, IntakeDocument
from app.services.patient_resolver import (
    build_patient_directory,
    index_intake_documents,
    match_intake_to_charts,
)
from app.services.audit_service import log_action
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.config import settings

router = APIRouter(prefix="/intake", tags=["intake"])

logger = logging.getLogger(__name__)


def _safe_counts(obj):
    """Numeric/bool-only view of a stats dict — keeps PHI out of logs."""
    return ({k: v for k, v in obj.items() if isinstance(v, (int, float, bool))}
            if isinstance(obj, dict) else type(obj).__name__)


# ── Patient Directory (chart_number -> name/dob) ─────────────────────────────

@router.post("/build-directory")
def build_directory(background_tasks: BackgroundTasks,
                     _: dict = Depends(requires_tier(Module.CHART, Tier.MANAGE))):
    """Walk Phreesia Demographic PDFs and populate the patient directory."""
    if not os.path.isdir(settings.documents_dir):
        raise HTTPException(status_code=404, detail=f"Documents dir not found: {settings.documents_dir}")
    background_tasks.add_task(_bg_build_directory)
    return {"status": "building", "source": settings.documents_dir}


def _bg_build_directory():
    db = SessionLocal()
    try:
        result = build_patient_directory(db)
        logger.info("[intake] directory build complete: %s", _safe_counts(result))
    finally:
        db.close()


@router.get("/directory/status")
def directory_status(
    db: Session = Depends(get_db),
    _: dict = Depends(requires_tier(Module.CHART, Tier.VIEW)),
):
    total = db.query(func.count(PatientDirectory.chart_number)).scalar() or 0
    with_dob = db.query(func.count(PatientDirectory.chart_number)).filter(
        PatientDirectory.dob.isnot(None)
    ).scalar() or 0
    return {"total_charts": total, "with_dob": with_dob}


@router.get("/directory")
def list_directory(
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(100, le=500),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.CHART, Tier.VIEW)),
):
    q = db.query(PatientDirectory)
    if search:
        s = f"%{search}%"
        q = q.filter(
            (PatientDirectory.patient_name.ilike(s)) |
            (PatientDirectory.chart_number.ilike(s))
        )
    total = q.count()
    rows = q.order_by(PatientDirectory.last_name, PatientDirectory.first_name)\
            .offset((page - 1) * per_page).limit(per_page).all()
    # One audit row per request — directory returns name+DOB+gender for
    # every match. (Fable intake audit #2.)
    log_action(db, "PATIENT_DIRECTORY_LIST", "intake_directory",
               user_name=current_user.get("email"),
               description=f"Directory list search={search} results={total}")
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "patients": [
            {
                "chart_number": r.chart_number,
                "patient_name": r.patient_name,
                "first_name": r.first_name,
                "last_name": r.last_name,
                "dob": str(r.dob) if r.dob else None,
                "gender": r.gender,
            }
            for r in rows
        ],
    }


# ── Intake Archives ──────────────────────────────────────────────────────────

@router.post("/index")
def start_indexing(
    background_tasks: BackgroundTasks,
    _: dict = Depends(requires_tier(Module.CHART, Tier.MANAGE)),
):
    """Walk the intake archive directory and index all files.

    Directory is configured server-side via INTAKE_DIR env var (default
    ~/Downloads/wwc_intake_docs). Previously accepted as a request
    param — an admin could point the indexer at any directory on the
    box. (Fable intake audit #10.)
    """
    intake_dir = os.environ.get("INTAKE_DIR", "~/Downloads/wwc_intake_docs")
    abs_dir = os.path.expanduser(intake_dir)
    if not os.path.isdir(abs_dir):
        raise HTTPException(status_code=404, detail=f"Directory not found: {abs_dir}")
    background_tasks.add_task(_bg_index_and_match, abs_dir)
    return {"status": "indexing", "directory": abs_dir}


def _bg_index_and_match(intake_dir: str):
    db = SessionLocal()
    try:
        idx = index_intake_documents(db, intake_dir)
        logger.info("[intake] index complete: %s", _safe_counts(idx))
        # Auto-match if directory is populated
        if db.query(PatientDirectory).count() > 0:
            match = match_intake_to_charts(db)
            logger.info("[intake] match complete: %s", _safe_counts(match))
    finally:
        db.close()


@router.post("/match")
def run_matching(db: Session = Depends(get_db),
                  current_user: dict = Depends(requires_tier(Module.CHART, Tier.WORK))):
    """Re-run matching against the current patient directory."""
    result = match_intake_to_charts(db)
    log_action(db, "INTAKE_MATCH", "intake", actor=current_user,
               description=f"Matching run: {result}")
    return result


@router.get("/status")
def intake_status(
    db: Session = Depends(get_db),
    _: dict = Depends(requires_tier(Module.CHART, Tier.VIEW)),
):
    """Summary of intake document index and match state."""
    total = db.query(func.count(IntakeDocument.id)).scalar() or 0
    if total == 0:
        return {"total": 0, "match_summary": {}}

    exact = db.query(func.count(IntakeDocument.id)).filter(IntakeDocument.match_confidence == "exact").scalar() or 0
    fuzzy_high = db.query(func.count(IntakeDocument.id)).filter(IntakeDocument.match_confidence == "fuzzy_high").scalar() or 0
    fuzzy_low = db.query(func.count(IntakeDocument.id)).filter(IntakeDocument.match_confidence == "fuzzy_low").scalar() or 0
    dob_no_name = db.query(func.count(IntakeDocument.id)).filter(IntakeDocument.match_confidence == "dob_no_name").scalar() or 0
    unmatched = db.query(func.count(IntakeDocument.id)).filter(IntakeDocument.match_confidence == "unmatched").scalar() or 0
    pending = db.query(func.count(IntakeDocument.id)).filter(IntakeDocument.match_confidence == "pending").scalar() or 0

    unique_patients = db.query(func.count(distinct(
        func.concat(IntakeDocument.patient_name_raw, "-", IntakeDocument.dob)
    ))).scalar() or 0

    by_category = db.query(
        IntakeDocument.doc_category,
        func.count(IntakeDocument.id)
    ).group_by(IntakeDocument.doc_category).all()

    return {
        "total": total,
        "unique_patients": unique_patients,
        "match_summary": {
            "exact": exact,
            "fuzzy_high": fuzzy_high,
            "fuzzy_low": fuzzy_low,
            "dob_no_name": dob_no_name,
            "unmatched": unmatched,
            "pending": pending,
        },
        "by_category": [{"category": c or "Other", "count": n} for c, n in by_category],
    }


@router.get("/documents")
def list_intake_documents(
    name: Optional[str] = None,
    dob: Optional[str] = None,
    category: Optional[str] = None,
    match_confidence: Optional[str] = None,
    chart_number: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, le=200),
    db: Session = Depends(get_db),
    _: dict = Depends(requires_tier(Module.CHART, Tier.VIEW)),
):
    """List intake documents with filters."""
    q = db.query(IntakeDocument)
    if name:
        q = q.filter(IntakeDocument.patient_name_raw.ilike(f"%{name}%"))
    if dob:
        from datetime import date as _date
        try:
            q = q.filter(IntakeDocument.dob == _date.fromisoformat(dob))
        except ValueError:
            # 422 instead of silently dropping the filter — without this,
            # a typo'd DOB returns every intake patient's documents when
            # the user thinks they're filtering. (Fable intake audit #8.)
            raise HTTPException(status_code=422,
                                detail=f"dob must be YYYY-MM-DD; got {dob!r}")
    if category:
        q = q.filter(IntakeDocument.doc_category == category)
    if match_confidence:
        q = q.filter(IntakeDocument.match_confidence == match_confidence)
    if chart_number:
        q = q.filter(IntakeDocument.matched_chart_number == chart_number)

    total = q.count()
    rows = q.order_by(IntakeDocument.patient_name_raw, IntakeDocument.dob)\
            .offset((page - 1) * per_page).limit(per_page).all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "documents": [_intake_to_dict(d) for d in rows],
    }


@router.get("/patients")
def list_intake_patients(
    match_confidence: Optional[str] = None,
    db: Session = Depends(get_db),
    _: dict = Depends(requires_tier(Module.CHART, Tier.VIEW)),
):
    """
    List unique patients in the intake archive with their match status
    and document counts.
    """
    q = db.query(
        IntakeDocument.patient_name_raw,
        IntakeDocument.dob,
        IntakeDocument.matched_chart_number,
        IntakeDocument.match_confidence,
        IntakeDocument.match_score,
        func.count(IntakeDocument.id).label("doc_count"),
    )
    if match_confidence:
        q = q.filter(IntakeDocument.match_confidence == match_confidence)

    rows = q.group_by(
        IntakeDocument.patient_name_raw,
        IntakeDocument.dob,
        IntakeDocument.matched_chart_number,
        IntakeDocument.match_confidence,
        IntakeDocument.match_score,
    ).order_by(IntakeDocument.patient_name_raw).all()

    return {
        "total": len(rows),
        "patients": [
            {
                "name": r.patient_name_raw,
                "dob": str(r.dob) if r.dob else None,
                "chart_number": r.matched_chart_number,
                "match_confidence": r.match_confidence,
                "match_score": float(r.match_score or 0),
                "doc_count": r.doc_count,
            }
            for r in rows
        ],
    }


@router.patch("/documents/{doc_id}/override-match")
def override_match(doc_id: str, chart_number: str, db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.CHART, Tier.WORK))):
    """Manually assign a chart number to an intake document (or all docs for that patient)."""
    doc = db.query(IntakeDocument).filter(IntakeDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Verify chart exists
    chart = db.query(PatientDirectory).filter(PatientDirectory.chart_number == chart_number).first()
    if not chart:
        raise HTTPException(status_code=404, detail=f"Chart {chart_number} not found in directory")

    # Apply to ALL documents for this patient (same name+dob)
    affected = db.query(IntakeDocument).filter(
        IntakeDocument.patient_name_raw == doc.patient_name_raw,
        IntakeDocument.dob == doc.dob,
    ).update({
        IntakeDocument.matched_chart_number: chart_number,
        IntakeDocument.match_confidence: "manual",
        IntakeDocument.match_score: 1.0,
    })
    db.commit()

    log_action(
        db, "INTAKE_MANUAL_MATCH", "intake_document",
        actor=current_user,
        resource_id=doc_id,
        description=f"Manual match: {doc.patient_name_raw} ({doc.dob}) -> chart {chart_number}, {affected} docs updated"
    )
    return {"status": "ok", "docs_updated": affected, "chart_number": chart_number}


def _intake_media_type(doc) -> str:
    if doc.file_type in ("jpg", "jpeg"):
        return "image/jpeg"
    if doc.file_type == "png":
        return "image/png"
    if doc.file_type == "docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/pdf"


@router.get("/download/{doc_id}")
def download_intake(
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.CHART, Tier.VIEW)),
):
    doc = db.query(IntakeDocument).filter(IntakeDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")

    log_action(
        db, "DOWNLOAD", "intake_document",
        resource_id=doc_id,
        user_name=current_user.get("email"),
        description=f"Downloaded intake {doc.filename} for {doc.patient_name_raw}"
    )
    # _intake_gcs_object returns "" on resolve failure; serve_blob would
    # 500. Surface a clean 404 instead. (Fable intake audit #5.)
    gcs_obj = _intake_gcs_object(doc.file_path) if using_gcs() else None
    if using_gcs() and not gcs_obj:
        raise HTTPException(status_code=404, detail="Document not in storage")
    return serve_blob(
        local_path=doc.file_path if not using_gcs() else None,
        gcs_object=gcs_obj,
        media_type=_intake_media_type(doc),
        filename=doc.filename,
        disposition="attachment",
    )


@router.get("/view/{doc_id}")
def view_intake(
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.CHART, Tier.VIEW)),
):
    doc = db.query(IntakeDocument).filter(IntakeDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")

    # Viewing a PHI document is the same PHI access as downloading it —
    # audit accordingly. (Fable intake audit #2.)
    log_action(
        db, "VIEW", "intake_document",
        resource_id=doc_id,
        user_name=current_user.get("email"),
        description=f"Viewed intake {doc.filename} for {doc.patient_name_raw}"
    )
    gcs_obj = _intake_gcs_object(doc.file_path) if using_gcs() else None
    if using_gcs() and not gcs_obj:
        raise HTTPException(status_code=404, detail="Document not in storage")
    return serve_blob(
        local_path=doc.file_path if not using_gcs() else None,
        gcs_object=gcs_obj,
        media_type=_intake_media_type(doc),
        filename=doc.filename,
        disposition="inline",
    )


def _intake_to_dict(d: IntakeDocument) -> dict:
    return {
        "id": str(d.id),
        "patient_name": d.patient_name_raw,
        "dob": str(d.dob) if d.dob else None,
        "doc_category": d.doc_category,
        "doc_year": d.doc_year,
        "filename": d.filename,
        "file_type": d.file_type,
        "file_size_kb": d.file_size_kb,
        "matched_chart_number": d.matched_chart_number,
        "match_confidence": d.match_confidence,
        "match_score": float(d.match_score or 0),
    }
