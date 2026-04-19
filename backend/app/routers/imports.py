"""File import router — handles ERA 835, CSV, XLS, PDF uploads."""

import os
import uuid
from datetime import datetime
from fastapi import APIRouter, File, UploadFile, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.parsers.file_importer import import_file
from app.services.era_import_service import import_era_file
from app.services.audit_service import log_action

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload and import a file (ERA 835, CSV, XLS/XLSX, PDF)."""
    content = await file.read()
    filename = file.filename or "upload"

    # Save to disk
    os.makedirs(settings.upload_dir, exist_ok=True)
    file_id = str(uuid.uuid4())
    ext = os.path.splitext(filename)[1]
    save_path = os.path.join(settings.upload_dir, f"{file_id}{ext}")
    with open(save_path, "wb") as f:
        f.write(content)

    # Parse
    result = import_file(save_path, content_bytes=content)

    if not result.success:
        log_action(db, "IMPORT", "file", resource_id=file_id,
                   description=f"Failed import: {filename}",
                   status="failure", error_detail=str(result.errors))
        raise HTTPException(status_code=422, detail={
            "errors": result.errors,
            "filename": filename,
            "format": result.format,
        })

    response = {
        "file_id": file_id,
        "filename": filename,
        "format": result.format,
        "detected_type": result.detected_type,
        "row_count": result.row_count,
        "success": True,
        "save_path": save_path,
    }

    # For ERA files, persist to DB automatically
    if result.format == "era835" and result.era_data:
        era_file = import_era_file(db, result.era_data, save_path)
        response["era_file_id"] = str(era_file.id)
        response["claims_imported"] = era_file.transaction_count
        response["payer"] = era_file.payer_name
        response["check_number"] = era_file.check_number
        response["check_amount"] = float(era_file.check_amount or 0)
        log_action(db, "IMPORT", "era_file", resource_id=str(era_file.id),
                   description=f"ERA import: {filename} — {era_file.transaction_count} claims")
    else:
        # For non-ERA files, return the parsed data for user review before import
        response["data_preview"] = (result.tabular_data or [])[:20]
        response["text_preview"] = (result.text_content or "")[:500]
        response["total_rows"] = result.row_count
        log_action(db, "IMPORT", "file", resource_id=file_id,
                   description=f"File import: {filename} ({result.format}, {result.row_count} rows)")

    return response


@router.get("/era-files")
def list_era_files(db: Session = Depends(get_db)):
    from app.models.claim import EraFile
    from sqlalchemy import desc
    files = db.query(EraFile).order_by(desc(EraFile.imported_at)).limit(100).all()
    return [
        {
            "id": str(f.id),
            "filename": f.filename,
            "payer_name": f.payer_name,
            "check_number": f.check_number,
            "check_date": str(f.check_date) if f.check_date else None,
            "check_amount": float(f.check_amount or 0),
            "transaction_count": f.transaction_count,
            "status": f.status,
            "imported_at": f.imported_at.isoformat() if f.imported_at else None,
        }
        for f in files
    ]
