"""Bank reconciliation API — CSV → review → BAI2 generator + history.

Two-step flow:
  1) POST /preview   — upload CSV, return parsed transactions for review
  2) POST /generate  — build the BAI2 file from approved transactions
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date as _date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.bai2 import Bai2Import, Bai2Transaction
from app.models.bai2_exclusion import Bai2Exclusion
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_super_admin, requires_tier
from app.services.bai2_generator import (
    FilterOptions, parse_csv_from_bytes, render_bai2, make_filename,
)
from app.services.storage import (
    save_blob, save_blob_with_key, serve_blob, read_blob, is_legacy_local_path,
)


log = logging.getLogger(__name__)

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
# Identity-based auto-exclusion (date + amount + last_4)
#
# A candidate is auto-excluded only when its (transaction_date, amount,
# last_4) matches a transaction ALREADY stored in a prior BAI2 file. This
# catches re-worded duplicates (the bank rewrites the same deposit between
# exports) WITHOUT dropping a genuinely-new transaction that merely shares
# a posting date with a prior import — e.g. a late-posting deposit, or one
# that was pending in an earlier file and has since cleared.

def _q2(a) -> Decimal:
    """Normalize a money value to a 2-dp Decimal so float vs Decimal and
    representation noise (123.4 vs 123.40 vs 123.400001) compare equal."""
    return Decimal(str(a if a is not None else 0)).quantize(Decimal("0.01"))


def _prior_identities(db: Session) -> set:
    """Set of (transaction_date, _q2(amount), last_4 or "") over ALL stored
    Bai2Transaction rows (across every import, deleted or not — the rows
    persist so the same bank transaction can't be re-posted)."""
    rows = db.query(
        Bai2Transaction.transaction_date,
        Bai2Transaction.amount,
        Bai2Transaction.last_4,
    ).all()
    return {(d, _q2(a), (l4 or "")) for (d, a, l4) in rows}


def _identity(t) -> tuple:
    """Identity tuple for a parsed candidate (ParsedTransaction)."""
    return (t.transaction_date, _q2(t.amount), (t.last_4 or ""))


def _exclusion_key(d, amt, l4) -> str:
    """Stable sha256 key for a sticky exclusion identity. Mirrors the
    (date, amount, last_4) identity so re-excluding the same bank
    transaction upserts the same row."""
    import hashlib
    raw = f"{d}|{_q2(amt)}|{l4 or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _active_exclusion_identities(db: Session) -> set:
    """Set of (transaction_date, _q2(amount), last_4 or "") over ACTIVE
    sticky exclusions (deleted_at IS NULL — reinstated rows don't block)."""
    rows = db.query(
        Bai2Exclusion.transaction_date,
        Bai2Exclusion.amount,
        Bai2Exclusion.last_4,
    ).filter(Bai2Exclusion.deleted_at.is_(None)).all()
    return {(d, _q2(a), (l4 or "")) for (d, a, l4) in rows}


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
    # preview_csv writes the uploaded CSV to GCS (and the snapshot.json)
    # — that's a side-effect on shared storage, not a read. Align the
    # gate with /generate (Tier.WORK) so VIEW-tier users can't push
    # arbitrary blobs into the bank-recon-csv/ prefix. (Fable design
    # review note 2.)
    current_user: dict = Depends(requires_tier(Module.BANK_RECON, Tier.WORK)),
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

    # Identity-based auto-exclusion: a candidate is "already imported" only
    # when its (date, amount, last_4) matches a transaction already stored
    # in a prior BAI2 file. This catches re-worded duplicates (the bank
    # rewrites the same deposit between exports — ACH DEP… vs …HCCLAIMPMT…)
    # while letting through genuinely-new transactions that merely share a
    # posting date with a prior import (late posts, cleared-after-pending).
    prior = _prior_identities(db)

    txns = []
    already_imported_keys = []
    for t in parsed.transactions:
        already = _identity(t) in prior
        if already:
            already_imported_keys.append(t.dedup_key)
        txns.append({
            "dedup_key": t.dedup_key,
            "date": str(t.transaction_date),
            "description": t.description,
            "formatted_text": t.formatted_text,
            "amount": t.amount,
            "method": t.method,
            "last_4": t.last_4,
            "already_imported": already,
        })

    # Persist the filter snapshot so /generate consumes exactly what the
    # user approved. Without this, the client could (legitimately or
    # via a stale tab) re-send different skip_* flags to /generate and
    # the system would generate a different transaction set than the
    # user reviewed. (Fable cross-cutting audit #12.) The
    # already_imported_keys list records the identity decision the user
    # previewed so /generate enforces the same set.
    snapshot = {
        "preview_id": preview_id,
        "ext": ext,
        "filters": {
            "skip_withdrawals": skip_withdrawals,
            "skip_modmed": skip_modmed,
            "skip_stripe": skip_stripe,
            "skip_zero": skip_zero,
        },
        "candidate_dedup_keys": [t.dedup_key for t in parsed.transactions],
        "already_imported_keys": already_imported_keys,
    }
    save_blob_with_key(
        key=f"bank-recon-csv/{preview_id}.snapshot.json",
        body=json.dumps(snapshot).encode("utf-8"),
        content_type="application/json",
    )

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
        },
        "transactions": txns,
    }


# ──────────────────────────────────────────────────────────────────────
# Step 2: GENERATE — build BAI2 from the reviewed/approved transactions

class GenerateRequest(BaseModel):
    # preview_id is generated server-side as uuid.uuid4().hex (32 lower
    # hex chars). ext is constrained so the csv key can't escape the
    # bank-recon-csv/ prefix via `../something`. (Fable cross-cutting
    # audit #25, design review note 7.)
    preview_id: str = Field(pattern=r"^[a-fA-F0-9]{32}$")
    csv_filename: str
    ext: Literal[".csv", ".txt"] = ".csv"
    bank_name: str = "PNC x395"
    account_full: Optional[str] = None
    excluded_keys: List[str] = []      # dedup_keys the user unchecked
    skip_withdrawals: bool = True
    skip_modmed: bool = True
    skip_stripe: bool = True
    skip_zero: bool = True


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
    import hashlib
    _lock_key = int(hashlib.sha1(
        payload.preview_id.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                 {"k": _lock_key})
    # Idempotency lookup: a soft-deleted import should NOT count as a
    # cache hit — the user explicitly deleted it, so a re-generate is
    # a fresh row. The downstream date-coverage check intentionally
    # does NOT filter deleted because the dedup_key transactions remain
    # and would still catch re-imports.
    prior = (db.query(Bai2Import)
                .filter(Bai2Import.csv_path == csv_key,
                        Bai2Import.not_deleted())
                .first())
    if prior is not None:
        return {**_import_to_dict(prior),
                "skipped_user_excluded": 0,
                "deduped": True}

    try:
        csv_bytes = read_blob(csv_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="preview CSV not found — re-upload")

    # Load the server-side filter snapshot so /generate produces exactly
    # the same transaction set the user reviewed at /preview. The client
    # still sends skip_* flags but they're a fall-back; the snapshot
    # wins when available. (Fable cross-cutting audit #12.)
    snapshot = None
    try:
        snap_bytes = read_blob(f"bank-recon-csv/{payload.preview_id}.snapshot.json")
        snapshot = json.loads(snap_bytes.decode("utf-8"))
    except FileNotFoundError:
        log.warning("BAI2 generate: no snapshot for preview %s — falling "
                    "back to client-supplied filters", payload.preview_id)

    snap_filters = (snapshot or {}).get("filters") or {}
    filters = FilterOptions(
        skip_withdrawals=snap_filters.get("skip_withdrawals", payload.skip_withdrawals),
        skip_modmed=snap_filters.get("skip_modmed", payload.skip_modmed),
        skip_stripe=snap_filters.get("skip_stripe", payload.skip_stripe),
        skip_zero=snap_filters.get("skip_zero", payload.skip_zero),
    )
    parsed = parse_csv_from_bytes(csv_bytes, filters)

    excluded = set(payload.excluded_keys or [])

    # Identity-based auto-exclusion: a candidate is a prior duplicate only
    # when its (date, amount, last_4) matches a transaction already stored
    # in a prior BAI2 file. This catches re-worded duplicates while letting
    # through genuinely-new transactions that merely share a posting date
    # with a prior import. Everything not a true duplicate (and not manually
    # excluded by the user) is imported, regardless of date. Manual
    # exclusion still wins. (Within-file dedup + dedup_key formula
    # unchanged — that's the storage unique-constraint identity.)
    prior = _prior_identities(db)

    new_txns = [
        t for t in parsed.transactions
        if t.dedup_key not in excluded and _identity(t) not in prior
    ]
    skipped_user_excluded = sum(1 for t in parsed.transactions if t.dedup_key in excluded)
    skipped_prior = sum(
        1 for t in parsed.transactions
        if t.dedup_key not in excluded and _identity(t) in prior
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
        .filter(Bai2Import.not_deleted())
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
    imp = (db.query(Bai2Import)
             .filter(Bai2Import.id == import_id,
                     Bai2Import.not_deleted())
             .first())
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
    imp = (db.query(Bai2Import)
             .filter(Bai2Import.id == import_id,
                     Bai2Import.not_deleted())
             .first())
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
    """Soft-delete a Bai2Import. The row is marked deleted but kept;
    its Bai2Transaction children (and their dedup_keys) stay intact so
    the same bank transactions can't be re-imported and re-posted.
    Restore via POST /imports/{id}/restore. (Fable design review notes
    4 and 13.)
    """
    from app.services.audit_service import log_action
    imp = (db.query(Bai2Import)
             .filter(Bai2Import.id == import_id,
                     Bai2Import.not_deleted())
             .first())
    if not imp:
        raise HTTPException(status_code=404, detail="import not found")
    log_action(
        db, action="BAI2_IMPORT_DELETED", resource_type="bai2_import",
        actor=current_user,
        resource_id=str(imp.id),
        description=(f"Soft-deleted BAI2 import {imp.csv_filename or imp.id} "
                     f"({imp.transactions_included} txns, "
                     f"${imp.total_amount or 0:.2f}). Transactions retained — "
                     "restore via POST /imports/{id}/restore."),
        defer_commit=True,
    )
    imp.soft_delete(by_email=current_user.get("email"))
    db.commit()
    return {"deleted": True, "id": str(imp.id)}


@router.post("/imports/{import_id}/restore")
def restore_import(
    import_id: str, db: Session = Depends(get_db),
    current_user: dict = Depends(
        requires_tier(Module.BANK_RECON, Tier.MANAGE)),
):
    """Undo a soft-delete. The import becomes visible to listings again
    and its transactions resume blocking re-imports."""
    from app.services.audit_service import log_action
    imp = db.query(Bai2Import).filter(Bai2Import.id == import_id).first()
    if not imp:
        raise HTTPException(status_code=404, detail="import not found")
    if not imp.is_deleted:
        return {"restored": False, "id": str(imp.id), "reason": "not deleted"}
    log_action(
        db, action="BAI2_IMPORT_RESTORED", resource_type="bai2_import",
        actor=current_user, resource_id=str(imp.id),
        description=f"Restored BAI2 import {imp.csv_filename or imp.id}",
        defer_commit=True,
    )
    imp.restore()
    db.commit()
    return {"restored": True, "id": str(imp.id)}


@router.post("/sweep-preview-csvs")
def sweep_preview_csvs(
    db: Session = Depends(get_db),
    ttl_hours: int = Query(24, ge=1, le=24 * 30),
    hard_ttl_days: int = Query(7, ge=1, le=90),
    current_user: dict = Depends(requires_super_admin()),
):
    """Manual trigger for the bank-recon-csv/ sweep. The primary scheduled
    runner is the `bank_recon_sweep` Cloud Run Job (Cloud Scheduler →
    hourly). This endpoint is kept for one-off ops triggering, hence
    super-admin only — not a coordinator workflow. (Fable design review
    note 6.)
    """
    from app.services.bank_recon_sweep import sweep_preview_csvs as _sweep
    return _sweep(db, ttl_hours=ttl_hours, hard_ttl_days=hard_ttl_days)
