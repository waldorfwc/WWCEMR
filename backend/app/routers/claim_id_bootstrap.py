"""POST /imports/claim-id-bootstrap (upload/preview + commit).

Two-step flow: upload a Claims Analysis .xls, preview matches, commit.
Commit patches patient_control_number on primary matches and creates
new secondary/tertiary Claim rows where Claims Analysis shows them.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.auth import get_current_user
from app.services import import_sessions
from app.services.claims_analysis_matcher import (
    ClaimsAnalysisImport, MatchResult, match_groups, parse,
)


router = APIRouter(prefix="/imports", tags=["claim-id-bootstrap"])
SESSION_TTL_MIN = 30


def _to_jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if is_dataclass(v):
        return {k: _to_jsonable(x) for k, x in asdict(v).items()}
    if isinstance(v, list):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _to_jsonable(x) for k, x in v.items()}
    return v


def _summarize(results: list) -> dict:
    out = {
        "will_patch": 0, "will_create_secondary": 0, "already_set": 0,
        "no_patient": 0, "no_claim": 0, "ambiguous": 0, "conflicts": 0,
    }
    for r in results:
        if r.status == "conflict":
            out["conflicts"] += 1
        else:
            out[r.status] = out.get(r.status, 0) + 1
    return out


@router.post("/claim-id-bootstrap")
async def upload_claims_analysis(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xls", ".xlsx"):
        raise HTTPException(status_code=422, detail="file must be .xls or .xlsx")
    session_id = str(uuid.uuid4())
    subdir = os.path.join(settings.upload_dir, "claim_id_bootstrap")
    os.makedirs(subdir, exist_ok=True)
    save_path = os.path.join(subdir, f"{session_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as fh:
        fh.write(content)

    try:
        parsed: ClaimsAnalysisImport = parse(save_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"could not read Excel file: {exc}")

    parsed.source_filename = file.filename or parsed.source_filename
    results = match_groups(db, parsed.groups)
    summary = _summarize(results)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=SESSION_TTL_MIN)
    import_sessions.put(import_sessions.SessionEntry(
        session_id=session_id,
        payload={"parsed": parsed, "results": results},
        filename=parsed.source_filename,
        file_path=save_path,
        user_email=current_user.get("email"),
        created_at=now,
        expires_at=expires_at,
    ))

    return {
        "session_id": session_id,
        "source_filename": parsed.source_filename,
        "total_rows": parsed.total_rows,
        "skipped_rows": parsed.skipped_rows,
        "unique_claims": len(parsed.groups),
        **summary,
        "sample_matches": [_to_jsonable(r) for r in results[:20]],
        "issues": [_to_jsonable(i) for i in parsed.issues],
        "expires_at": expires_at.isoformat(),
    }
