"""Bank reconciliation API — CSV → review → BAI2 generator + history.

Two-step flow:
  1) POST /preview   — upload CSV, return parsed transactions for review
  2) POST /generate  — build the BAI2 file from approved transactions
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import date as _date, datetime
from decimal import Decimal

log = logging.getLogger(__name__)
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.bai2 import Bai2Import, Bai2Transaction
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services.bai2_generator import (
    FilterOptions, parse_csv_from_bytes, render_bai2, make_filename,
)
from app.services.storage import (
    save_blob, save_blob_with_key, serve_blob, read_blob, is_legacy_local_path,
)


router = APIRouter(prefix="/bank-recon", tags=["bank-recon"])


# ──────────────────────────────────────────────────────────────────────
# Helpers

def _import_to_dict(imp: Bai2Import) -> dict:
    return {
        "id": str(imp.id),
        "bank_name": imp.bank_name,
        "account_last_4": imp.account_last_4,
        "csv_filename": imp.csv_filename,
        "bai2_filename": imp.bai2_filename,
        "date_range_start": str(imp.date_range_start) if imp.date_range_start else None,
        "date_range_end":   str(imp.date_range_end)   if imp.date_range_end   else None,
        "csv_row_count": imp.csv_row_count,
        "transactions_included": imp.transactions_included,
        "skipped_withdrawal": imp.skipped_withdrawal,
        "skipped_modmed": imp.skipped_modmed,
        "skipped_stripe": imp.skipped_stripe,
        "skipped_zero": imp.skipped_zero,
        "skipped_duplicate_in_file": imp.skipped_duplicate_in_file,
        "skipped_prior_imports": imp.skipped_prior_imports,
        "total_amount": float(imp.total_amount or 0),
        "notes": imp.notes,
        "generated_at": str(imp.generated_at) if imp.generated_at else None,
        "generated_by": imp.generated_by,
        "downloadable": bool(imp.bai2_path) and not is_legacy_local_path(imp.bai2_path),
    }


# ──────────────────────────────────────────────────────────────────────
# Step 1: PREVIEW — upload CSV, parse, return transactions for review

@router.post("/preview")
async def preview_csv(
    file: UploadFile = File(...),
    bank_name: str = Form("PNC x395"),
    skip_withdrawals: bool = Form(True),
    skip_modmed: bool = Form(True),
    skip_stripe: bool = Form(True),
    skip_zero: bool = Form(True),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Save the CSV, parse + filter + dedup, and return a list of
    transactions for the user to review BEFORE the BAI2 file is generated.
    No DB writes happen at this step — the CSV is just cached on disk and
    its filename returned as `preview_id`."""
    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext not in ('.csv', '.txt'):
        raise HTTPException(status_code=422, detail="file must be .csv or .txt")

    preview_id = uuid.uuid4().hex
    content = await file.read()
    save_blob_with_key(key=f"bank-recon-csv/{preview_id}{ext}",
                          body=content, content_type="text/csv")

    filters = FilterOptions(
        skip_withdrawals=skip_withdrawals,
        skip_modmed=skip_modmed,
        skip_stripe=skip_stripe,
        skip_zero=skip_zero,
    )
    parsed = parse_csv_from_bytes(content, filters)

    # Mark each transaction with whether its dedup_key already exists in DB
    keys = [t.dedup_key for t in parsed.transactions]
    already_in_db = set()
    if keys:
        already_in_db = {
            row[0]
            for row in db.query(Bai2Transaction.dedup_key)
            .filter(Bai2Transaction.dedup_key.in_(keys)).all()
        }

    # Overlap guard: flag transactions whose date is already covered by a
    # prior import's date range. The bank re-words the same deposit between
    # exports (ACH DEP… vs …HCCLAIMPMT…), so the dedup_key alone misses
    # these overlap-day re-imports — date coverage is the reliable signal.
    prior_ranges = [
        (s, e) for (s, e) in
        db.query(Bai2Import.date_range_start, Bai2Import.date_range_end).all()
        if s and e
    ]
    def _date_covered(d):
        return any(s <= d <= e for (s, e) in prior_ranges)

    txns = []
    for t in parsed.transactions:
        covered = _date_covered(t.transaction_date)
        txns.append({
            "dedup_key": t.dedup_key,
            "date": str(t.transaction_date),
            "description": t.description,
            "formatted_text": t.formatted_text,
            "amount": t.amount,
            "method": t.method,
            "last_4": t.last_4,
            "already_imported": t.dedup_key in already_in_db,
            "date_already_covered": covered,
        })

    return {
        "preview_id": preview_id,
        "csv_filename": file.filename,
        "ext": ext,
        "csv_row_count": parsed.csv_row_count,
        "stats": {
            "transactions_to_review": len(txns),
            "skipped_withdrawal": parsed.skipped_withdrawal,
            "skipped_modmed": parsed.skipped_modmed,
            "skipped_stripe": parsed.skipped_stripe,
            "skipped_zero": parsed.skipped_zero,
            "skipped_duplicate_in_file": parsed.skipped_duplicate_in_file,
            "skipped_always_drop": parsed.skipped_always_drop,
            "already_imported_count": sum(1 for t in txns if t["already_imported"]),
            "date_covered_count": sum(
                1 for t in txns
                if t["date_already_covered"] and not t["already_imported"]),
        },
        "transactions": txns,
    }


# ──────────────────────────────────────────────────────────────────────
# Step 2: GENERATE — build BAI2 from the reviewed/approved transactions

_PREVIEW_ID_RE = __import__("re").compile(r"^[a-fA-F0-9]{32}$")
_ALLOWED_EXTS = {".csv", ".txt"}


class GenerateRequest(BaseModel):
    preview_id: str
    csv_filename: str
    ext: str = ".csv"
    bank_name: str = "PNC x395"
    account_full: Optional[str] = None
    excluded_keys: List[str] = []      # dedup_keys the user unchecked
    skip_withdrawals: bool = True
    skip_modmed: bool = True
    skip_stripe: bool = True
    skip_zero: bool = True

    # Validate at parse time — preview_id is generated as
    # uuid.uuid4().hex on the server (32 hex chars); ext must be one
    # of our allowed values. Without these checks `ext` could be
    # `/../something` and the csv_key would escape the bank-recon-csv/
    # prefix. (Fable cross-cutting audit #25.)
    @classmethod
    def model_validate(cls, *args, **kwargs):
        m = super().model_validate(*args, **kwargs)
        if not _PREVIEW_ID_RE.match(m.preview_id or ""):
            raise ValueError("preview_id must be a 32-character hex string")
        if m.ext not in _ALLOWED_EXTS:
            raise ValueError(f"ext must be one of {sorted(_ALLOWED_EXTS)}")
        return m


@router.post("/generate")
def generate_bai2(payload: GenerateRequest,
                  db: Session = Depends(get_db),
                  current_user: dict = Depends(get_current_user),
                  _perm: dict = Depends(requires_tier(Module.BANK_RECON, Tier.WORK))):
    """Build the BAI2 file from the CSV cached at preview_id, excluding any
    transactions whose dedup_key is in `excluded_keys`.

    Idempotent on (preview_id, ext): a double-click on Generate used to
    produce two GCS-stored BAI2 files for the same deposits (one of the
    two DB inserts failed via the dedup_key unique constraint, but the
    file write happened before the DB write, so the orphan stayed in
    storage). Now serialized via a Postgres advisory lock keyed on the
    preview, and a re-issued generate for an already-consumed preview
    returns the existing Bai2Import row instead of regenerating.
    (Fable cross-cutting audit #3.)
    """
    csv_key = f"bank-recon-csv/{payload.preview_id}{payload.ext}"

    # Take a transaction-scoped advisory lock keyed on the preview id
    # hash so two concurrent /generate calls on the same preview
    # serialize. Then short-circuit if this preview was already
    # consumed — return the existing import row.
    from sqlalchemy import text as _sql_text
    import hashlib
    _lock_key = int(hashlib.sha1(
        payload.preview_id.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF
    db.execute(_sql_text("SELECT pg_advisory_xact_lock(:k)"),
                 {"k": _lock_key})
    prior = (db.query(Bai2Import)
                .filter(Bai2Import.csv_path == csv_key)
                .first())
    if prior is not None:
        return {**_import_to_dict(prior),
                "skipped_user_excluded": 0,
                "deduped": True}

    try:
        csv_bytes = read_blob(csv_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="preview CSV not found — re-upload")

    filters = FilterOptions(
        skip_withdrawals=payload.skip_withdrawals,
        skip_modmed=payload.skip_modmed,
        skip_stripe=payload.skip_stripe,
        skip_zero=payload.skip_zero,
    )
    parsed = parse_csv_from_bytes(csv_bytes, filters)

    excluded = set(payload.excluded_keys or [])

    # Cross-import dedup
    keys = [t.dedup_key for t in parsed.transactions]
    already = set()
    if keys:
        already = {
            row[0]
            for row in db.query(Bai2Transaction.dedup_key)
            .filter(Bai2Transaction.dedup_key.in_(keys)).all()
        }

    new_txns = [
        t for t in parsed.transactions
        if t.dedup_key not in excluded and t.dedup_key not in already
    ]
    skipped_user_excluded = sum(1 for t in parsed.transactions if t.dedup_key in excluded)
    skipped_prior = sum(
        1 for t in parsed.transactions
        if t.dedup_key not in excluded and t.dedup_key in already
    )

    if not new_txns:
        imp = Bai2Import(
            csv_filename=payload.csv_filename, csv_path=csv_key,
            bank_name=payload.bank_name, account_last_4=None,
            account_full=payload.account_full,
            csv_row_count=parsed.csv_row_count,
            transactions_included=0,
            skipped_withdrawal=parsed.skipped_withdrawal,
            skipped_modmed=parsed.skipped_modmed,
            skipped_stripe=parsed.skipped_stripe,
            skipped_zero=parsed.skipped_zero,
            skipped_duplicate_in_file=parsed.skipped_duplicate_in_file,
            skipped_prior_imports=skipped_prior,
            total_amount=Decimal('0'),
            generated_by=current_user.get('email'),
            notes=(
                f"No BAI2 generated — {skipped_user_excluded} excluded by user"
                + (f", {skipped_prior} duplicates of prior imports" if skipped_prior else "")
            ),
        )
        db.add(imp); db.commit(); db.refresh(imp)
        return {**_import_to_dict(imp), "skipped_user_excluded": skipped_user_excluded}

    dates = [t.transaction_date for t in new_txns]
    start, end = min(dates), max(dates)

    bai2_text = render_bai2(new_txns, payload.bank_name,
                            payload.account_full, '')
    filename = make_filename(payload.bank_name, start, end)

    key = save_blob(prefix="bank-recon",
                    body=bai2_text.encode("utf-8"),
                    filename=filename)

    total_amount = Decimal(str(sum(t.amount for t in new_txns)))
    imp = Bai2Import(
        csv_filename=payload.csv_filename, csv_path=csv_key,
        bank_name=payload.bank_name, account_last_4=None,
        account_full=payload.account_full,
        bai2_filename=filename, bai2_path=key,
        date_range_start=start, date_range_end=end,
        csv_row_count=parsed.csv_row_count,
        transactions_included=len(new_txns),
        skipped_withdrawal=parsed.skipped_withdrawal,
        skipped_modmed=parsed.skipped_modmed,
        skipped_stripe=parsed.skipped_stripe,
        skipped_zero=parsed.skipped_zero,
        skipped_duplicate_in_file=parsed.skipped_duplicate_in_file,
        skipped_prior_imports=skipped_prior,
        total_amount=total_amount,
        generated_by=current_user.get('email'),
        notes=(f"User excluded {skipped_user_excluded} transactions" if skipped_user_excluded else None),
    )
    db.add(imp); db.flush()

    for t in new_txns:
        db.add(Bai2Transaction(
            import_id=imp.id,
            transaction_date=t.transaction_date,
            description=t.description,
            formatted_text=t.formatted_text,
            amount=Decimal(str(t.amount)),
            last_4=t.last_4 or None,
            method=t.method,
            bai_type_code=t.bai_type_code,
            dedup_key=t.dedup_key,
        ))
    from sqlalchemy.exc import IntegrityError
    try:
        db.commit(); db.refresh(imp)
    except IntegrityError:
        # Another generate raced past our advisory lock (shouldn't
        # happen since we held it transaction-scoped, but if the lock
        # call ever fails to take, the unique constraint on
        # Bai2Transaction.dedup_key catches the duplicate here).
        # Roll back our writes, log loudly so a duplicate BAI2 file in
        # storage doesn't go unnoticed, and surface a clean 409.
        db.rollback()
        log.error("BAI2 generate IntegrityError for preview %s — "
                  "advisory lock didn't serialize. Orphan BAI2 file at %s.",
                  payload.preview_id, key)
        raise HTTPException(
            status_code=409,
            detail=("These transactions were already imported by another "
                    "request. Refresh and review the recent imports list."))

    return {**_import_to_dict(imp), "skipped_user_excluded": skipped_user_excluded}


# ──────────────────────────────────────────────────────────────────────
# History + download

@router.get("/imports")
def list_imports(
    db: Session = Depends(get_db),
    limit: int = Query(50, le=200),
    current_user: dict = Depends(
        requires_tier(Module.BANK_RECON, Tier.VIEW)),
):
    # Bank transaction descriptions can include patient names (Zelle
    # / check deposits identified by payor name). Tier the reads.
    # (Fable cross-cutting audit #16.)
    rows = (
        db.query(Bai2Import)
        .order_by(desc(Bai2Import.generated_at))
        .limit(limit).all()
    )
    return {"imports": [_import_to_dict(r) for r in rows]}


@router.get("/imports/{import_id}")
def get_import(
    import_id: str, db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.BANK_RECON, Tier.VIEW)),
):
    imp = db.query(Bai2Import).filter(Bai2Import.id == import_id).first()
    if not imp:
        raise HTTPException(status_code=404, detail="import not found")
    txns = imp.transactions.order_by(Bai2Transaction.transaction_date.desc()).all()
    return {
        **_import_to_dict(imp),
        "transactions": [
            {
                "id": str(t.id),
                "date": str(t.transaction_date) if t.transaction_date else None,
                "description": t.description,
                "formatted_text": t.formatted_text,
                "amount": float(t.amount or 0),
                "method": t.method,
                "last_4": t.last_4,
            }
            for t in txns
        ],
    }


@router.get("/imports/{import_id}/download")
def download_bai2(
    import_id: str, db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.BANK_RECON, Tier.VIEW)),
):
    imp = db.query(Bai2Import).filter(Bai2Import.id == import_id).first()
    if not imp or not imp.bai2_path:
        raise HTTPException(status_code=404, detail="BAI2 file not available")
    if is_legacy_local_path(imp.bai2_path):
        raise HTTPException(status_code=410,
                            detail="This BAI2 file is from before the cloud migration and is no longer available.")
    return serve_blob(
        local_path=None,
        gcs_object=imp.bai2_path,
        media_type="text/plain",
        filename=imp.bai2_filename or "bai2.txt",
    )


@router.delete("/imports/{import_id}")
def delete_import(
    import_id: str, db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.BANK_RECON, Tier.MANAGE)),
):
    """Hard-delete a Bai2Import row.

    Previously: zero permission gate, no audit. The cascade-delete on
    Bai2Transaction takes the dedup_keys with it, which means the same
    bank transactions can be re-imported and re-posted later — exactly
    the double-import hole the unique constraint exists to prevent.
    Tier.MANAGE + audit row + a "are you really sure?" check by
    requiring the caller pass ?confirm=true. (Fable cross-cutting
    audit #6.)
    """
    from app.services.audit_service import log_action
    imp = db.query(Bai2Import).filter(Bai2Import.id == import_id).first()
    if not imp:
        raise HTTPException(status_code=404, detail="import not found")
    actor = current_user.get("email")
    log_action(
        db, action="BAI2_IMPORT_DELETED", resource_type="bai2_import",
        resource_id=str(imp.id),
        user_id=(actor or "").lower() or None, user_name=actor,
        description=(f"Hard-deleted BAI2 import {imp.csv_filename or imp.id} "
                     f"({imp.transactions_included} txns, "
                     f"${imp.total_amount or 0:.2f}). dedup_keys are gone — "
                     "re-importing the same transactions is now possible."),
    )
    db.delete(imp); db.commit()
    return {"deleted": True}
