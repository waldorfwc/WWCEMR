"""POST /imports/era-posting (multi-file ERA upload/preview + commit)."""
from __future__ import annotations

import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.parsers.era_835 import Era835Parser
from app.routers.auth import get_current_user, require_permission
from app.services.idempotency import idempotency_for
from app.services import import_sessions
from app.services.era_poster import EraFilePreview, build_preview
from app.services.audit_service import log_action


router = APIRouter(prefix="/imports", tags=["era-posting"])
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


def _file_summary(p: EraFilePreview) -> dict:
    return {
        "source_filename": p.source_filename,
        "check_number": p.era.check_number,
        "check_amount": float(p.era.check_amount or 0),
        "check_date": p.era.check_date.isoformat() if p.era.check_date else None,
        "payer_name": p.era.payer_name,
        "n_claims": len(p.era.claims),
        "n_matched": p.n_matched,
        "n_unmatched": p.n_unmatched,
        "n_already_posted": p.n_already_posted,
        "n_cb_skipped": p.n_cb_skipped,
        "n_reversals": p.n_reversals,
        "n_malformed": p.n_malformed,
        "parse_errors": list(p.era.parse_errors or []),
    }


@router.post("/era-posting")
async def upload_eras(
    file: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not file:
        raise HTTPException(status_code=422, detail="at least one file required")
    for f in file:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in (".835", ".x12", ".edi", ""):
            raise HTTPException(status_code=422,
                                detail=f"file {f.filename!r} not a supported ERA format")

    session_id = str(uuid.uuid4())
    subdir = os.path.join(settings.upload_dir, "era_posting", session_id)
    os.makedirs(subdir, exist_ok=True)

    previews: List[EraFilePreview] = []
    for idx, f in enumerate(file):
        content_bytes = await f.read()
        save_path = os.path.join(subdir, f"{idx}-{f.filename or 'era.835'}")
        with open(save_path, "wb") as fh:
            fh.write(content_bytes)
        try:
            content = content_bytes.decode("utf-8", errors="ignore")
            if "ISA" not in content[:500]:
                raise HTTPException(status_code=422,
                                    detail=f"{f.filename!r} does not look like an ERA 835")
            era = Era835Parser().parse(content, filename=f.filename or f"era{idx}.835")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422,
                                detail=f"could not parse {f.filename!r}: {exc}")
        prev = build_preview(db, era, source_filename=f.filename or f"era{idx}.835")
        prev.era.filename = f.filename or prev.era.filename
        prev.__dict__["_file_path"] = save_path
        previews.append(prev)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=SESSION_TTL_MIN)
    import_sessions.put(import_sessions.SessionEntry(
        session_id=session_id,
        payload={"previews": previews},
        filename=f"era_batch_{len(previews)}_files",
        file_path=subdir,
        user_email=current_user.get("email"),
        created_at=now, expires_at=expires_at,
    ))

    totals = {
        "n_files": len(previews),
        "combined_check_amount": sum(float(p.era.check_amount or 0) for p in previews),
        "n_matched": sum(p.n_matched for p in previews),
        "n_unmatched": sum(p.n_unmatched for p in previews),
        "n_already_posted": sum(p.n_already_posted for p in previews),
        "n_cb_skipped": sum(p.n_cb_skipped for p in previews),
        "n_reversals": sum(p.n_reversals for p in previews),
        "n_malformed": sum(p.n_malformed for p in previews),
    }

    sample = []
    for p in previews:
        for m in p.matches[:5]:
            sample.append({
                "source_filename": p.source_filename,
                "status": m.status,
                "internal_claim_id": m.internal_claim_id,
                "billed_amount": float(m.era_claim.billed_amount or 0),
                "paid_amount": float(m.era_claim.paid_amount or 0),
                "reversal_reason": m.reversal_reason,
            })

    issues = []
    for p in previews:
        for m in p.matches:
            if m.status in ("unmatched", "reversal_flagged", "malformed_clp01"):
                issues.append({
                    "source_filename": p.source_filename,
                    "status": m.status,
                    "internal_claim_id": m.internal_claim_id,
                    "billed_amount": float(m.era_claim.billed_amount or 0),
                    "reason": m.reversal_reason or None,
                })

    return {
        "session_id": session_id,
        "files": [_file_summary(p) for p in previews],
        "totals": totals,
        "sample_matches": sample,
        "issues": issues,
        "expires_at": expires_at.isoformat(),
    }


from app.services.era_poster import process_era_file


@router.post("/era-posting/{session_id}/commit")
def commit_eras(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    _perm: dict = Depends(require_permission("payment:post")),
    idem=Depends(idempotency_for("POST /era-posting/{session_id}/commit")),
):
    # If the client sent an Idempotency-Key header and we've already
    # processed this commit, return the cached response and skip
    # re-processing — double-clicks no longer double-post.
    if idem.cached is not None:
        return idem.cached

    entry = import_sessions.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="session not found or expired")

    previews: List[EraFilePreview] = entry.payload["previews"]
    user_email = current_user.get("email")

    totals = {
        "claims_posted": 0, "claims_already_posted": 0, "claims_unmatched": 0,
        "claims_reversal_flagged": 0, "claims_cb_skipped": 0, "claims_malformed": 0,
        "payments_created": 0, "denials_created": 0,
    }
    errors: list = []

    for p in previews:
        file_path = p.__dict__.get("_file_path", "")
        try:
            with open(file_path, "r") as f:
                content = f.read()
        except OSError as exc:
            errors.append({"filename": p.source_filename,
                           "message": f"could not re-read file: {exc}"})
            continue
        result = process_era_file(db, content, p.source_filename, user_email)
        totals["claims_posted"] += result.claims_posted
        totals["claims_already_posted"] += result.claims_already_posted
        totals["claims_unmatched"] += result.claims_unmatched
        totals["claims_reversal_flagged"] += result.claims_reversal_flagged
        totals["claims_cb_skipped"] += result.claims_cb_skipped
        totals["claims_malformed"] += result.claims_malformed
        totals["payments_created"] += result.payments_created
        totals["denials_created"] += result.denials_created
        errors.extend(result.errors)

    import_sessions.purge(session_id)

    response = {
        "files_processed": len(previews),
        "claims_posted": totals["claims_posted"],
        "claims_already_posted": totals["claims_already_posted"],
        "claims_unmatched": totals["claims_unmatched"],
        "claims_reversal_flagged": totals["claims_reversal_flagged"],
        "claims_cb_skipped": totals["claims_cb_skipped"],
        "claims_malformed": totals["claims_malformed"],
        "payments_created": totals["payments_created"],
        "denials_created": totals["denials_created"],
        "errors": errors,
    }
    # Cache the response so a retry with the same Idempotency-Key returns
    # this body instead of re-running the post loop.
    idem.store(response)
    log_action(
        db,
        action="ERA_COMMIT",
        resource_type="era_session",
        resource_id=session_id,
        user_id=(user_email or "").lower() or None,
        user_name=user_email,
        description=(f"ERA commit: {len(previews)} files, "
                     f"{totals['claims_posted']} claims posted, "
                     f"{totals['payments_created']} payments, "
                     f"{totals['denials_created']} denials"),
    )
    db.commit()   # persist the idempotency row alongside the era results
    return response
