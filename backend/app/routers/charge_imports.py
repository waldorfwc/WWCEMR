"""Two-step Charge Analysis import: POST upload (preview), POST {id}/commit."""
from __future__ import annotations

import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.models.claim import Claim
from app.models.patient import Patient
from app.services.audit_service import log_action
from app.services import import_sessions
from app.services.charge_analysis_importer import (
    ChargeAnalysisImport, ParsedClaim, parse,
)
from app.routers.auth import get_current_user


router = APIRouter(prefix="/imports", tags=["charge-imports"])

SESSION_TTL_MIN = 30


def _claim_to_jsonable(c: ParsedClaim) -> Dict[str, Any]:
    """Convert a ParsedClaim dataclass tree to JSON-safe types."""
    def _j(v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        if hasattr(v, "isoformat"):
            return v.isoformat()
        if is_dataclass(v):
            return {k: _j(x) for k, x in asdict(v).items()}
        if isinstance(v, list):
            return [_j(x) for x in v]
        if isinstance(v, dict):
            return {k: _j(x) for k, x in v.items()}
        return v
    return _j(c)


def _compute_flags(
    parsed: ChargeAnalysisImport, db: Session
) -> List[Dict[str, Any]]:
    """For each parsed claim, resolve existing-claim + existing-patient flags."""
    visit_ids = [c.visit_id for c in parsed.claims]
    existing = {
        row.claim_number for row in db.query(Claim.claim_number)
        .filter(Claim.claim_number.in_(visit_ids)).all()
    } if visit_ids else set()

    patient_ids = [c.patient_external_id for c in parsed.claims if c.patient_external_id]
    existing_patients = {
        row.patient_id: str(row.id) for row in db.query(Patient.patient_id, Patient.id)
        .filter(Patient.patient_id.in_(patient_ids)).all()
    } if patient_ids else {}

    flags = []
    for c in parsed.claims:
        resolved = existing_patients.get(c.patient_external_id)
        flags.append({
            "visit_id": c.visit_id,
            "exists_in_db": c.visit_id in existing,
            "patient_resolved_id": resolved,
            "will_create_patient": resolved is None,
        })
    return flags


@router.post("/charge-analysis")
async def upload_charge_analysis(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # Persist the upload under upload_dir/charge_analysis/<session_id><ext>
    session_id = str(uuid.uuid4())
    subdir = os.path.join(settings.upload_dir, "charge_analysis")
    os.makedirs(subdir, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xls", ".xlsx"):
        raise HTTPException(status_code=422, detail="file must be .xls or .xlsx")
    save_path = os.path.join(subdir, f"{session_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as fh:
        fh.write(content)

    # Parse (pure function)
    try:
        parsed = parse(save_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422,
                            detail=f"could not read Excel file: {exc}")

    # Use the original uploaded filename (not the UUID-based on-disk name)
    # so the preview reflects what the user selected.
    original_filename = file.filename or parsed.source_filename
    parsed.source_filename = original_filename

    # Dedup / patient-resolution flags
    flags = _compute_flags(parsed, db)
    will_create = sum(1 for f in flags if not f["exists_in_db"])
    will_skip_existing = sum(1 for f in flags if f["exists_in_db"])
    will_match_patients = sum(1 for f in flags if not f["will_create_patient"])
    will_create_patients = sum(1 for f in flags if f["will_create_patient"])
    errors = sum(1 for i in parsed.issues if i.severity == "error")
    warnings = sum(1 for i in parsed.issues if i.severity == "warning")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=SESSION_TTL_MIN)

    import_sessions.put(import_sessions.SessionEntry(
        session_id=session_id,
        payload=parsed,
        filename=file.filename or "charge_analysis.xls",
        file_path=save_path,
        user_email=current_user.get("email"),
        created_at=now,
        expires_at=expires_at,
        claim_flags=flags,
    ))

    return {
        "session_id": session_id,
        "source_filename": parsed.source_filename,
        "total_rows": parsed.total_rows,
        "parsed_claims": len(parsed.claims),
        "skipped_voids": parsed.skipped_voids,
        "skipped_non_clinical": parsed.skipped_non_clinical,   # NEW — F.Chg rows
        "will_create": will_create,
        "will_skip_existing": will_skip_existing,
        "will_create_patients": will_create_patients,
        "will_match_patients": will_match_patients,
        "errors": errors,
        "warnings": warnings,
        "sample_claims": [_claim_to_jsonable(c) for c in parsed.claims[:20]],
        "issues": [
            {
                "severity": i.severity,
                "row_index": i.row_index,
                "visit_id": i.visit_id,
                "message": i.message,
            }
            for i in parsed.issues
        ],
        "expires_at": expires_at.isoformat(),
    }
