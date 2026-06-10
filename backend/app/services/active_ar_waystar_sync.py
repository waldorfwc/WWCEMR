"""Sync claim status from Waystar for active AR claims.

For each claim:
 1. Call WaystarClient.get_claim_status(claim_number, payer_id)
 2. Persist the raw response to ActiveClaim.last_status_response
 3. Update ActiveClaim.last_status_check_at = now
 4. Append an ActiveClaimNote with the parsed summary
 5. Auto-attach an existing ERA file as an ActiveClaimDocument when the
    response carries a check# that matches an ERA already in our DB
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from app.utils.dt import now_utc_naive
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.active_ar import ActiveClaim, ActiveClaimNote, ActiveClaimDocument
from app.models.claim import EraFile
from app.services.waystar_service import WaystarClient


def _summarize_response(response: Dict) -> Tuple[str, dict]:
    """Pull the most useful fields out of the (variable-shape) Waystar
    response. Returns (one-line summary, normalized dict)."""
    # Try several field names — Waystar's payload shape isn't 100% stable
    # across endpoints.
    status = (
        response.get("status")
        or response.get("claimStatus")
        or response.get("statusCode")
        or response.get("claim_status")
    )
    paid_amount = (
        response.get("paid_amount")
        or response.get("paidAmount")
        or response.get("payment", {}).get("amount") if isinstance(response.get("payment"), dict) else None
    )
    paid_date = (
        response.get("paid_date")
        or response.get("paidDate")
        or response.get("payment", {}).get("date") if isinstance(response.get("payment"), dict) else None
    )
    check_number = (
        response.get("check_number")
        or response.get("checkNumber")
        or response.get("payment", {}).get("checkNumber") if isinstance(response.get("payment"), dict) else None
    )
    denial_codes = response.get("denial_codes") or response.get("denialCodes") or []
    error = response.get("error")

    parts = []
    if error:
        parts.append(f"⚠ {error}")
    if status:
        parts.append(f"Status: {status}")
    if paid_amount:
        parts.append(f"Paid ${paid_amount}")
    if paid_date:
        parts.append(f"on {paid_date}")
    if check_number:
        parts.append(f"check #{check_number}")
    if denial_codes:
        parts.append(f"Denials: {', '.join(map(str, denial_codes))}")

    summary = " · ".join(parts) if parts else "(no parsable fields in response)"

    return summary, {
        "status": status,
        "paid_amount": paid_amount,
        "paid_date": paid_date,
        "check_number": check_number,
        "denial_codes": denial_codes,
        "error": error,
    }


def _find_matching_era(db: Session, normalized: dict) -> Optional[EraFile]:
    """Look up an ERA already in our DB that matches the response's check#."""
    check_number = normalized.get("check_number")
    if not check_number:
        return None
    return db.query(EraFile).filter(EraFile.check_number == str(check_number)).first()


def _attach_era_as_document(
    db: Session, claim: ActiveClaim, era: EraFile, user_email: Optional[str]
) -> Optional[ActiveClaimDocument]:
    """Auto-attach a matched ERA file as an ActiveClaimDocument if not
    already attached. The ERA's on-disk file is referenced by file_path —
    we don't copy bytes."""
    # De-dupe: skip if this ERA is already attached
    for existing in claim.documents:
        if existing.file_path == era.file_path:
            return None
    if not era.file_path or not os.path.exists(era.file_path):
        return None
    doc = ActiveClaimDocument(
        active_claim_id=claim.id,
        document_type="ERA",
        filename=era.filename or os.path.basename(era.file_path),
        content_type="application/octet-stream",
        file_size=os.path.getsize(era.file_path),
        file_path=era.file_path,
        description=(
            f"Auto-attached from Waystar status sync · "
            f"check #{era.check_number or '—'} · "
            f"${float(era.check_amount or 0):.2f} · "
            f"{era.payer_name or 'Unknown payer'}"
        ),
        uploaded_by=user_email or "system",
    )
    db.add(doc)
    return doc


def sync_one(
    db: Session, claim: ActiveClaim,
    waystar: Optional[WaystarClient] = None,
    user_email: Optional[str] = None,
) -> dict:
    """Sync a single claim. Commits its own changes."""
    waystar = waystar or WaystarClient()
    response = waystar.get_claim_status(
        claim_number=claim.claim_number,
        payer_id=claim.payor_id,
    ) or {}

    summary, normalized = _summarize_response(response)

    # Persist the raw response (truncated if pathologically large)
    raw = json.dumps(response, default=str)[:20_000]
    claim.last_status_response = raw
    claim.last_status_check_at = now_utc_naive()

    note_lines = [summary]
    era_attached = None

    era = _find_matching_era(db, normalized)
    if era is not None:
        attached = _attach_era_as_document(db, claim, era, user_email)
        if attached is not None:
            era_attached = era.filename
            note_lines.append(
                f"ERA auto-attached: {era.filename} (check #{era.check_number}, "
                f"${float(era.check_amount or 0):.2f})"
            )
        else:
            note_lines.append(
                f"ERA already attached: {era.filename}"
            )

    db.add(ActiveClaimNote(
        active_claim_id=claim.id,
        user=user_email or "system",
        action_type="status_check",
        note="\n".join(note_lines),
    ))
    db.commit()

    return {
        "claim_id": str(claim.id),
        "claim_number": claim.claim_number,
        "summary": summary,
        "normalized": normalized,
        "era_attached": era_attached,
    }


def sync_many(
    db: Session, claims: List[ActiveClaim],
    user_email: Optional[str] = None,
    max_count: int = 100,
) -> dict:
    """Sync up to `max_count` claims. Continues on per-claim errors."""
    waystar = WaystarClient()
    results = []
    errors = []
    attached_count = 0
    for c in claims[:max_count]:
        try:
            r = sync_one(db, c, waystar=waystar, user_email=user_email)
            results.append(r)
            if r.get("era_attached"):
                attached_count += 1
        except Exception as exc:
            db.rollback()
            errors.append({"claim_id": str(c.id), "error": f"{type(exc).__name__}: {exc}"})

    return {
        "synced_count": len(results),
        "era_attached_count": attached_count,
        "errors": errors,
        "results": results,
    }
