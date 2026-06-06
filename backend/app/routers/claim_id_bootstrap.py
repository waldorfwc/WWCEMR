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

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services import import_sessions
from app.services.claims_analysis_fixer import fix_file as fix_claims_analysis
from app.services.claims_analysis_matcher import (
    ClaimsAnalysisImport, MatchResult, match_groups, parse,
)
from app.services.import_drift import (
    KEYS_AND_VALUES, check_drift, compute_fingerprints, write_audit_log,
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
    autofix_shifts: bool = Query(
        True,
        description="Drop PrimeSuite phantom-empty columns and realign "
                    "row-level shifts before parsing. Transparent "
                    "pass-through when the file is already clean.",
    ),
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

    # Optional pre-processor: drop phantom columns + realign shifts.
    autofix_report = None
    parse_path = save_path
    if autofix_shifts:
        fixed_path = os.path.join(subdir, f"{session_id}.fixed.xlsx")
        try:
            report = fix_claims_analysis(save_path, fixed_path)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"autofix-shifts failed: {exc}",
            )
        if report.shifts_detected:
            parse_path = fixed_path
            autofix_report = {
                "applied": True,
                "phantom_columns_dropped": report.phantom_columns_dropped,
                "unresolved_rows": report.unresolved_rows,
                "unresolved_samples": report.unresolved_samples,
            }
        else:
            try:
                os.remove(fixed_path)
            except OSError:
                pass
            autofix_report = {"applied": False, "reason": "no shifts detected"}

    try:
        parsed: ClaimsAnalysisImport = parse(parse_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"could not read Excel file: {exc}")

    parsed.source_filename = file.filename or parsed.source_filename
    results = match_groups(db, parsed.groups)
    summary = _summarize(results)

    # Drift check vs prior import for the same DOS range
    import pandas as _pd
    drift_dict = None
    period_start = period_end = None
    fingerprints: list = []
    drift = None
    try:
        df_for_drift = _pd.read_excel(parse_path, sheet_name=0)
    except Exception:
        df_for_drift = None
    if df_for_drift is not None:
        if "Date of Service" in df_for_drift.columns:
            ds = _pd.to_datetime(df_for_drift["Date of Service"], errors="coerce").dropna()
            if len(ds) > 0:
                period_start = ds.min().date()
                period_end = ds.max().date()
        keyspec = KEYS_AND_VALUES["claims_analysis"]
        fingerprints = compute_fingerprints(
            df_for_drift, keyspec["key_columns"], keyspec["value_columns"],
        )
        drift = check_drift(db, "claims_analysis", period_start, period_end, fingerprints)
        drift_dict = {
            "has_prior_import": drift.has_prior_import,
            "prior_imported_at": drift.prior_imported_at,
            "prior_filename": drift.prior_filename,
            "rows_added": drift.rows_added,
            "rows_removed": drift.rows_removed,
            "rows_changed": drift.rows_changed,
            "interpretation": drift.interpretation,
            "sample_added": drift.sample_added,
            "sample_removed": drift.sample_removed,
        }

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
        aux={
            "drift_period_start": period_start,
            "drift_period_end": period_end,
            "drift_fingerprints": fingerprints,
            "drift_report": drift,
        },
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
        "autofix": autofix_report,
        "drift": drift_dict,
        "expires_at": expires_at.isoformat(),
    }


from app.models.claim import Claim, ServiceLine, ClaimStatus, InsuranceOrder
from app.services.audit_service import log_action
from app.services.claim_math import recompute_balance


def _patch_claim(db: Session, claim_id: str, group: "ClaimsAnalysisGroup",
                 user_email: str) -> Claim:
    from app.services.claims_analysis_matcher import map_claim_status

    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    mapped_status = map_claim_status(group.claim_status_raw)

    old = {
        "patient_control_number": claim.patient_control_number,
        "status": claim.status.value if claim.status else None,
        "follow_up_date": str(claim.follow_up_date) if claim.follow_up_date else None,
        "follow_up_reason": claim.follow_up_reason,
        "last_submission_date": str(claim.last_submission_date) if claim.last_submission_date else None,
        "claim_state": claim.claim_state,
    }

    claim.patient_control_number = group.internal_claim_id
    if mapped_status is not None:
        claim.status = mapped_status
    claim.follow_up_date = group.follow_up_date
    claim.follow_up_reason = group.follow_up_reason
    claim.last_submission_date = group.last_submission_date
    claim.claim_state = group.claim_state
    db.commit()

    new = {
        "patient_control_number": group.internal_claim_id,
        "status": claim.status.value if claim.status else None,
        "follow_up_date": str(claim.follow_up_date) if claim.follow_up_date else None,
        "follow_up_reason": claim.follow_up_reason,
        "last_submission_date": str(claim.last_submission_date) if claim.last_submission_date else None,
        "claim_state": claim.claim_state,
    }
    log_action(
        db, "UPDATE", "claim",
        resource_id=str(claim.id),
        patient_id=str(claim.patient_id) if claim.patient_id else None,
        user_name=user_email,
        old_values=old, new_values=new,
        description="claim-id-bootstrap: patched workflow fields",
    )
    return claim


def _create_secondary(db: Session, primary_id: str, group, user_email: str) -> Claim:
    from app.services.claims_analysis_matcher import map_claim_status

    primary = db.query(Claim).filter(Claim.id == primary_id).first()
    order_map = {"secondary": InsuranceOrder.SECONDARY,
                 "tertiary": InsuranceOrder.TERTIARY}
    new_order = order_map.get(group.insurance_priority, InsuranceOrder.SECONDARY)
    mapped_status = map_claim_status(group.claim_status_raw)

    secondary = Claim(
        claim_number=primary.claim_number,
        patient_id=primary.patient_id,
        date_of_service_from=primary.date_of_service_from,
        date_of_service_to=primary.date_of_service_to,
        payer_name=primary.payer_name,
        payer_id=primary.payer_id,
        subscriber_id=primary.subscriber_id,
        rendering_provider_name=primary.rendering_provider_name,
        rendering_provider_npi=primary.rendering_provider_npi,
        billing_provider_npi=primary.billing_provider_npi,
        insurance_order=new_order,
        status=mapped_status or ClaimStatus.PENDING,
        billed_amount=primary.billed_amount,
        patient_control_number=group.internal_claim_id,
        # Phase 2d workflow fields
        follow_up_date=group.follow_up_date,
        follow_up_reason=group.follow_up_reason,
        last_submission_date=group.last_submission_date,
        claim_state=group.claim_state,
    )
    db.add(secondary); db.flush()
    primary_lines = db.query(ServiceLine).filter(ServiceLine.claim_id == primary.id).all()
    for sl in primary_lines:
        db.add(ServiceLine(
            claim_id=secondary.id,
            procedure_code=sl.procedure_code,
            modifier_1=sl.modifier_1, modifier_2=sl.modifier_2,
            modifier_3=sl.modifier_3, modifier_4=sl.modifier_4,
            units=sl.units,
            billed_amount=sl.billed_amount,
            date_of_service_from=sl.date_of_service_from,
            date_of_service_to=sl.date_of_service_to,
            diagnosis_codes=list(sl.diagnosis_codes or []),
        ))
    recompute_balance(secondary)
    db.commit()
    db.refresh(secondary)
    log_action(
        db, "CREATE", "claim",
        resource_id=str(secondary.id),
        patient_id=str(secondary.patient_id) if secondary.patient_id else None,
        user_name=user_email,
        new_values={
            "claim_number": secondary.claim_number,
            "insurance_order": new_order.value,
            "patient_control_number": group.internal_claim_id,
            "status": secondary.status.value if secondary.status else None,
            "follow_up_date": str(secondary.follow_up_date) if secondary.follow_up_date else None,
            "follow_up_reason": secondary.follow_up_reason,
            "last_submission_date": str(secondary.last_submission_date) if secondary.last_submission_date else None,
            "claim_state": secondary.claim_state,
        },
        description=f"claim-id-bootstrap: created {new_order.value} claim from primary",
    )
    return secondary


@router.post("/claim-id-bootstrap/{session_id}/commit")
def commit_claim_id_bootstrap(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    entry = import_sessions.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="session not found or expired")

    results: list[MatchResult] = entry.payload["results"]
    user_email = current_user.get("email")

    claims_patched = 0
    secondary_claims_created = 0
    already_set = 0
    unmatched = 0
    ambiguous = 0
    conflicts = 0
    errors: list[dict] = []

    for r in results:
        try:
            if r.status == "will_patch":
                _patch_claim(db, r.matched_claim_id, r.group, user_email)
                claims_patched += 1
            elif r.status == "will_create_secondary":
                _create_secondary(db, r.matched_claim_id, r.group, user_email)
                secondary_claims_created += 1
            elif r.status == "already_set":
                already_set += 1
            elif r.status in ("no_patient", "no_claim"):
                unmatched += 1
            elif r.status == "ambiguous":
                ambiguous += 1
            elif r.status == "conflict":
                conflicts += 1
        except Exception as exc:
            db.rollback()
            errors.append({"claim_id": r.group.claim_id,
                           "message": f"{type(exc).__name__}: {exc}"})

    log_action(
        db, "IMPORT", "claim_id_bootstrap",
        resource_id=session_id, user_name=user_email,
        description=(f"{entry.filename} — {claims_patched} patched, "
                     f"{secondary_claims_created} secondary created, "
                     f"{already_set} already set, {unmatched} unmatched"),
    )

    # Persist drift fingerprints for the next pull to compare against.
    fingerprints = entry.aux.get("drift_fingerprints") or []
    if fingerprints:
        try:
            parsed = entry.payload.get("parsed") if isinstance(entry.payload, dict) else None
            write_audit_log(
                db,
                report_type="claims_analysis",
                period_start=entry.aux.get("drift_period_start"),
                period_end=entry.aux.get("drift_period_end"),
                source_filename=entry.filename,
                file_path=entry.file_path,
                fingerprints=fingerprints,
                drift_report=entry.aux.get("drift_report"),
                row_count=parsed.total_rows if parsed else 0,
                imported_by=user_email,
            )
            db.commit()
        except Exception:
            db.rollback()

    import_sessions.purge(session_id)

    return {
        "source_filename": entry.filename,
        "claims_patched": claims_patched,
        "secondary_claims_created": secondary_claims_created,
        "already_set": already_set,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "conflicts": conflicts,
        "errors": errors,
    }
