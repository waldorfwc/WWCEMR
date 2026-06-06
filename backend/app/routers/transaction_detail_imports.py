"""POST /imports/transaction-detail — validation/preview endpoint for the
PrimeSuite Transaction Detail report.

For now this endpoint is **validation-only**: it reads the file, checks
the expected fields are present, runs drift detection against any prior
import for the same period, and returns a checksum-style report (row
count, dollar totals, drift summary). It does NOT yet write any
Patient / Claim / ServiceLine / Payment / Adjustment rows — that's the
next phase. The point of this endpoint is to validate the Q4 2025 pull
and prove the data lines up before we commit to ingesting it.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services.import_drift import (
    KEYS_AND_VALUES, check_drift, compute_fingerprints, file_sha256,
    write_audit_log,
)
from app.services.transaction_detail_fixer import fix_file as fix_td_file
from app.services.transaction_detail_importer import parse as parse_td


router = APIRouter(prefix="/imports", tags=["transaction-detail"])


# Expected columns (matches the field list given to the user for the report
# builder). Insurance: Class/Company/etc. were dropped — PrimeSuite doesn't
# expose them on this report.
EXPECTED_COLUMNS = {
    # Identity & linking
    "Patient: Patient ID", "Patient: Name", "Patient: First Name",
    "Patient: Last Name", "Patient: Date Of Birth",
    "Transaction: Visit ID", "Transaction: Charge Ticket Number",
    "Transaction: Type",

    # Patient demographics (added for the consolidated report).
    # NOTE: "EMail" (capital M) matches PrimeSuite's actual column label.
    "Patient: Address Line 1", "Patient: Address Line 2", "Patient: City",
    "Patient: State", "Patient: Zip Code", "Patient: Phone Primary",
    "Patient: EMail", "Patient: Sex",

    # Procedure / clinical
    "Transaction: Procedure Code", "Transaction: Procedure Description",
    "Transaction: Procedure Modifiers", "Transaction: Diagnosis ICD10 Codes",
    "Transaction: Net Charge Units", "Transaction: Description",

    # Dates
    "Date: Date of Service", "Date: Posting Date", "Date: Create Date",
    "Date: Original Posting Date", "Date: Original Create Date",

    # Money — net amounts
    "Transaction: Amount - Net Charges",
    "Transaction: Amount - Net Adjustments",
    "Transaction: Amount - Net Payments",

    # Money — gross amounts
    "Transaction: Amount - Gross Charges",
    "Transaction: Amount - Gross Adjustments",
    "Transaction: Amount - Gross Insurance Payments",
    "Transaction: Amount - Gross Patient/Other Payments",
    "Transaction: Amount - Gross Payments",

    # Money — voids & offsets
    "Transaction: Amount - Charge Voids",
    "Transaction: Amount - Adjustment Voids",
    "Transaction: Amount - Adjustment Offsets",
    "Transaction: Amount - Payment Voids",
    "Transaction: Amount - Payment Offsets",
    "Transaction: Amount - Transaction Amount",

    # Adjustment / payment classification
    "Transaction: Adjustment Type", "Transaction: Adjustment Sub-Type",
    "Transaction: Applied To", "Transaction: Payment Method",
    "Transaction: Payment Supplier", "Transaction: Payment/Adjustment Source",
    "Transaction: Payment/Adjustment Additional Info",

    # Reversal / linking.
    # NOTE: "Orginal" (sic) is PrimeSuite's actual column label — typo in their system.
    "Orginal Transaction: Transaction Amount",
    "Original Transaction: Payment/Adjustment Source",

    # Provider / location
    "Transaction: Billable Provider Name",
    "Transaction: Rendering Provider Name",
    "Transaction: Referring Provider Name",
    "Transaction: Practice Location", "Transaction: Service Location",

    # Audit & flags
    "Transaction: User", "Transaction: Void Indicator",
    "Transaction: ERA Indicator", "Transaction: Charge Override Indicator",
    "Transaction: Refund Check Number",
}


@router.post("/transaction-detail/validate")
async def validate_transaction_detail(
    file: UploadFile = File(...),
    period_start: Optional[str] = Query(
        None,
        description="Quarter start date YYYY-MM-DD. If omitted, derived from min posting date.",
    ),
    period_end: Optional[str] = Query(
        None,
        description="Quarter end date YYYY-MM-DD. If omitted, derived from max posting date.",
    ),
    persist_audit: bool = Query(
        True,
        description="Write the audit log + fingerprints so future re-imports can drift-check against this one.",
    ),
    autofix_shifts: bool = Query(
        True,
        description="Auto-realign PrimeSuite column shifts before validation.",
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Read a Transaction Detail file, validate columns, compute totals,
    run drift detection, and (optionally) record the audit log."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xls", ".xlsx", ".tbf"):
        raise HTTPException(status_code=422, detail="file must be .xls / .xlsx / .tbf")

    subdir = os.path.join(settings.upload_dir, "transaction_detail")
    os.makedirs(subdir, exist_ok=True)
    upload_id = str(uuid.uuid4())
    save_path = os.path.join(subdir, f"{upload_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as fh:
        fh.write(content)

    # Read the header row to validate column presence
    try:
        header_df = pd.read_excel(save_path, sheet_name=0, header=None, nrows=1)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"could not read Excel file: {exc}")

    headers = [str(h).strip() for h in header_df.iloc[0].tolist() if pd.notna(h)]
    headers_set = set(headers)
    missing = sorted(EXPECTED_COLUMNS - headers_set)
    extra = sorted(headers_set - EXPECTED_COLUMNS)

    # Optional autofix-shifts pre-processor.
    autofix_report = None
    parse_path = save_path
    if autofix_shifts and not missing:
        fixed_path = os.path.join(subdir, f"{upload_id}.fixed.xlsx")
        try:
            r = fix_td_file(save_path, fixed_path)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"autofix-shifts failed: {exc}")
        if r.shifts_detected:
            parse_path = fixed_path
            autofix_report = {
                "applied": True,
                "rows_realigned": r.rows_realigned,
                "unresolved_rows": r.unresolved_rows,
                "unresolved_samples": r.unresolved_samples,
            }
        else:
            try:
                os.remove(fixed_path)
            except OSError:
                pass
            autofix_report = {"applied": False, "reason": "no shifts detected"}

    # Read the full file with headers (possibly the fixed one)
    try:
        df = pd.read_excel(parse_path, sheet_name=0)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"could not read Excel data: {exc}")

    row_count = int(len(df))

    # Sanity totals
    def _safe_sum(col: str) -> Optional[float]:
        if col not in df.columns:
            return None
        return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())

    totals = {
        "net_charges":  _safe_sum("Transaction: Amount - Net Charges"),
        "net_adjustments": _safe_sum("Transaction: Amount - Net Adjustments"),
        "net_payments": _safe_sum("Transaction: Amount - Net Payments"),
        "gross_charges": _safe_sum("Transaction: Amount - Gross Charges"),
        "gross_insurance_payments": _safe_sum("Transaction: Amount - Gross Insurance Payments"),
        "gross_patient_payments": _safe_sum("Transaction: Amount - Gross Patient/Other Payments"),
    }

    # Derive period dates from data if not supplied
    pstart, pend = _parse_iso(period_start), _parse_iso(period_end)
    if pstart is None or pend is None:
        if "Date: Posting Date" in df.columns:
            posted = pd.to_datetime(df["Date: Posting Date"], errors="coerce").dropna()
            if len(posted) > 0:
                pstart = pstart or posted.min().date()
                pend = pend or posted.max().date()

    # Transaction-type breakdown
    type_counts = {}
    if "Transaction: Type" in df.columns:
        type_counts = (df["Transaction: Type"].fillna("(blank)").astype(str)
                       .value_counts().head(20).to_dict())

    # Compute fingerprints + run drift check
    keyspec = KEYS_AND_VALUES["transaction_detail"]
    fingerprints = compute_fingerprints(df, keyspec["key_columns"], keyspec["value_columns"])
    drift = check_drift(db, "transaction_detail", pstart, pend, fingerprints)

    audit_log_id: Optional[str] = None
    if persist_audit and missing == []:
        audit = write_audit_log(
            db,
            report_type="transaction_detail",
            period_start=pstart,
            period_end=pend,
            source_filename=file.filename or os.path.basename(save_path),
            file_path=save_path,
            fingerprints=fingerprints,
            drift_report=drift,
            row_count=row_count,
            total_amount=totals.get("net_charges"),
            secondary_total=totals.get("net_payments"),
            imported_by=current_user.get("email"),
        )
        db.commit()
        audit_log_id = str(audit.id)

    return {
        "filename": file.filename,
        "period_start": pstart.isoformat() if pstart else None,
        "period_end": pend.isoformat() if pend else None,
        "row_count": row_count,
        "totals": totals,
        "transaction_type_counts": type_counts,
        "schema": {
            "expected_count": len(EXPECTED_COLUMNS),
            "found_count": len(headers_set),
            "missing": missing,
            "extra": extra,
            "ok": missing == [],
        },
        "file_sha256": file_sha256(save_path),
        "autofix": autofix_report,
        "drift": _drift_to_dict(drift),
        "audit_log_id": audit_log_id,
        "note": (
            "Validation-only endpoint. Data is NOT yet written to "
            "Patients/Claims/ServiceLines/Payments. Use this to confirm "
            "the file is well-formed and to see drift vs. prior imports."
        ),
    }


@router.post("/transaction-detail/commit")
async def commit_transaction_detail(
    file: UploadFile = File(...),
    sources: str = Query(
        "Patient",
        description="Comma-separated source filter (Patient,Insurance,Unknown). "
                    "Default 'Patient' is the safe choice — won't double-post "
                    "vs. ERA-derived insurance payments.",
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Parse a Transaction Detail file and write Payment + Adjustment records
    linked to existing Claims. Skip CHG rows (use Charge Analysis for charges)."""
    from app.models.claim import Claim
    from app.models.patient import Patient
    from app.models.payment import Payment, PaymentType
    from app.services.audit_service import log_action

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xls", ".xlsx", ".tbf"):
        raise HTTPException(status_code=422, detail="file must be .xls / .xlsx / .tbf")

    subdir = os.path.join(settings.upload_dir, "transaction_detail")
    os.makedirs(subdir, exist_ok=True)
    upload_id = str(uuid.uuid4())
    save_path = os.path.join(subdir, f"{upload_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as fh:
        fh.write(content)

    try:
        parsed = parse_td(save_path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"parse failed: {exc}")

    allowed_sources = {s.strip().title() for s in sources.split(",") if s.strip()}

    # Cache patient external ID -> internal Patient.id
    pat_cache: dict[str, str] = {
        p.patient_id: str(p.id)
        for p in db.query(Patient.id, Patient.patient_id).all()
        if p.patient_id
    }
    # Cache (patient_internal_id, visit_id) -> claim.id
    claim_cache: dict[tuple, str] = {}
    for c in db.query(Claim.id, Claim.patient_id, Claim.claim_number).all():
        if c.patient_id and c.claim_number:
            claim_cache[(str(c.patient_id), c.claim_number)] = str(c.id)

    pmts_written = 0
    pmts_skipped_source = 0
    pmts_no_patient = 0
    pmts_no_claim = 0
    pmts_skipped_dup = 0
    by_source: dict[str, int] = {}

    user_email = current_user.get("email")

    for p in parsed.payments:
        by_source[p.source] = by_source.get(p.source, 0) + 1
        if p.source not in allowed_sources:
            pmts_skipped_source += 1
            continue

        patient_internal = pat_cache.get(p.patient_external_id)
        if not patient_internal:
            pmts_no_patient += 1
            continue

        claim_internal = None
        if p.visit_id:
            claim_internal = claim_cache.get((patient_internal, p.visit_id))
        if not claim_internal:
            # Patient payment without a claim is still valid (on-account credit).
            # Insurance payment without a claim is suspicious.
            if p.source == "Insurance":
                pmts_no_claim += 1
                continue

        # Dedup: same patient + claim + amount + posting_date + method
        existing = (
            db.query(Payment)
            .filter(
                Payment.patient_id == patient_internal,
                Payment.claim_id == claim_internal,
                Payment.amount == p.amount,
                Payment.payment_date == (p.payment_date or p.posting_date),
                Payment.payment_method == (p.method or ""),
            )
            .first()
        )
        if existing:
            pmts_skipped_dup += 1
            continue

        if p.source == "Insurance":
            ptype = PaymentType.INSURANCE_PAYMENT
        elif p.source == "Patient":
            # Detect if it's a copay/deductible from method+amount? Simple heuristic.
            ptype = PaymentType.PATIENT_PAYMENT
        else:
            ptype = PaymentType.ADJUSTMENT

        db.add(Payment(
            patient_id=patient_internal,
            claim_id=claim_internal,
            payment_type=ptype,
            amount=p.amount,
            payment_date=p.payment_date or p.posting_date,
            payer_name=("Patient" if p.source == "Patient" else None),
            check_number=p.payer_name,
            payment_method=p.method,
            posted_by=p.user or user_email,
            notes=f"Imported from Transaction Detail (source={p.source})",
        ))
        pmts_written += 1

    db.commit()

    log_action(
        db, "IMPORT", "transaction_detail",
        resource_id=upload_id, user_name=user_email,
        description=(
            f"{file.filename} — {pmts_written} payments written "
            f"({by_source}); {pmts_skipped_dup} dup-skipped, "
            f"{pmts_no_patient} no patient, {pmts_no_claim} no claim"
        ),
    )

    return {
        "filename": file.filename,
        "rows_processed": parsed.total_rows,
        "payments_parsed": len(parsed.payments),
        "payments_written": pmts_written,
        "payments_skipped_source": pmts_skipped_source,
        "payments_skipped_no_patient": pmts_no_patient,
        "payments_skipped_no_claim": pmts_no_claim,
        "payments_skipped_duplicate": pmts_skipped_dup,
        "by_source": by_source,
        "adjustments_parsed": len(parsed.adjustments),
        "note": "Adjustments are parsed but not yet committed (Phase 2). "
                "CHG rows are intentionally skipped — Charge Analysis is the truth source.",
    }


@router.get("/transaction-detail/audit")
def list_audit(
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List prior Transaction Detail imports. Useful for verifying
    historical pulls have been ingested + matched."""
    from app.models.import_audit import ImportAuditLog
    q = db.query(ImportAuditLog).filter(ImportAuditLog.report_type == "transaction_detail")
    if period_start:
        q = q.filter(ImportAuditLog.period_start == _parse_iso(period_start))
    if period_end:
        q = q.filter(ImportAuditLog.period_end == _parse_iso(period_end))
    rows = q.order_by(ImportAuditLog.imported_at.desc()).limit(limit).all()
    return {
        "audits": [
            {
                "id": str(r.id),
                "filename": r.source_filename,
                "period_start": r.period_start.isoformat() if r.period_start else None,
                "period_end": r.period_end.isoformat() if r.period_end else None,
                "row_count": r.row_count,
                "total_amount": float(r.total_amount) if r.total_amount is not None else None,
                "secondary_total": float(r.secondary_total) if r.secondary_total is not None else None,
                "rows_added": r.rows_added,
                "rows_removed": r.rows_removed,
                "rows_changed": r.rows_changed,
                "imported_by": r.imported_by,
                "imported_at": r.imported_at.isoformat() if r.imported_at else None,
                "file_sha256": r.file_sha256,
            }
            for r in rows
        ],
    }


def _parse_iso(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _drift_to_dict(d) -> dict:
    return {
        "has_prior_import": d.has_prior_import,
        "prior_import_id": d.prior_import_id,
        "prior_imported_at": d.prior_imported_at,
        "prior_filename": d.prior_filename,
        "rows_added": d.rows_added,
        "rows_removed": d.rows_removed,
        "rows_changed": d.rows_changed,
        "sample_added": d.sample_added,
        "sample_removed": d.sample_removed,
        "sample_changed": [
            {
                "natural_key": c.natural_key,
                "prior_value_hash": c.prior_value_hash,
                "new_value_hash": c.new_value_hash,
            } for c in d.sample_changed
        ],
        "interpretation": d.interpretation,
    }
