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


from app.models.claim import ServiceLine, ClaimStatus, InsuranceOrder
from app.services.claim_math import recompute_balance


def _summary_for_audit(claim: Claim) -> Dict[str, Any]:
    return {
        "claim_number": claim.claim_number,
        "payer_name": claim.payer_name,
        "billed_amount": float(claim.billed_amount or 0),
        "paid_amount": float(claim.paid_amount or 0),
        "patient_responsibility": float(claim.patient_responsibility or 0),
    }


def _ensure_patient(
    db: Session, parsed_claim: ParsedClaim, resolved_id: Optional[str]
) -> tuple[Optional[Patient], bool]:
    """Return (Patient-or-None, created_now).

    `created_now` is True only when this call inserted a new row, False when
    it reused an existing row (either matched via flag or created earlier in
    this same commit loop — the flag snapshot is stale once we start writing).
    """
    if resolved_id:
        return db.query(Patient).filter(Patient.id == resolved_id).first(), False
    demo = parsed_claim.patient_demographics or {}
    external = parsed_claim.patient_external_id
    if not external:
        return None, False
    # A patient with this external id may have been created earlier in THIS
    # same commit loop (the flag snapshot was taken at upload time).
    existing = db.query(Patient).filter(Patient.patient_id == external).first()
    if existing:
        return existing, False
    p = Patient(
        patient_id=external,
        first_name=demo.get("first_name"),
        last_name=demo.get("last_name"),
        date_of_birth=demo.get("date_of_birth"),
        phone=demo.get("phone"),
        address=demo.get("address"),
    )
    db.add(p)
    db.flush()  # assign p.id without committing the outer transaction
    return p, True


def _create_claim_with_lines(
    db: Session, parsed: ParsedClaim, patient: Optional[Patient]
) -> Claim:
    claim = Claim(
        claim_number=parsed.visit_id,
        patient_id=patient.id if patient else None,
        date_of_service_from=parsed.date_of_service_from,
        date_of_service_to=parsed.date_of_service_from,
        payer_name=parsed.payer_name,
        subscriber_id=parsed.subscriber_id,
        rendering_provider_name=parsed.rendering_provider_name,
        rendering_provider_npi=parsed.rendering_provider_npi,
        billing_provider_npi=parsed.billing_provider_npi,
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING,
        billed_amount=parsed.billed_amount,
        paid_amount=parsed.paid_amount,
        patient_responsibility=parsed.patient_responsibility,
        contractual_adjustment=parsed.contractual_adjustment,
        other_adjustment=parsed.other_adjustment,
    )
    db.add(claim)
    db.flush()
    for sl in parsed.service_lines:
        db.add(ServiceLine(
            claim_id=claim.id,
            procedure_code=sl.procedure_code,
            modifier_1=sl.modifier_1,
            modifier_2=sl.modifier_2,
            modifier_3=sl.modifier_3,
            modifier_4=sl.modifier_4,
            units=sl.units,
            billed_amount=sl.billed_amount,
            paid_amount=sl.paid_amount,
            patient_responsibility=sl.patient_responsibility,
            contractual_adjustment=sl.contractual_adjustment,
            other_adjustment=sl.other_adjustment,
            date_of_service_from=sl.date_of_service_from,
            date_of_service_to=sl.date_of_service_from,
            diagnosis_codes=list(sl.diagnosis_codes),
        ))
    recompute_balance(claim)
    return claim


@router.post("/charge-analysis/{session_id}/commit")
def commit_charge_analysis(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    entry = import_sessions.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="session not found or expired")

    parsed: ChargeAnalysisImport = entry.payload
    flags_by_vid = {f["visit_id"]: f for f in entry.claim_flags}

    claims_created = 0
    claims_skipped_existing = 0
    patients_created = 0
    patients_matched = 0
    service_lines_created = 0
    errors: List[Dict[str, Any]] = []
    user_email = current_user.get("email")

    for parsed_claim in parsed.claims:
        flag = flags_by_vid.get(parsed_claim.visit_id, {})
        if flag.get("exists_in_db"):
            claims_skipped_existing += 1
            continue

        try:
            patient, created_now = _ensure_patient(
                db, parsed_claim, flag.get("patient_resolved_id")
            )
            if patient is not None:
                if created_now:
                    patients_created += 1
                else:
                    patients_matched += 1

            claim = _create_claim_with_lines(db, parsed_claim, patient)
            db.commit()
            claims_created += 1
            service_lines_created += len(parsed_claim.service_lines)

            log_action(
                db, "CREATE", "claim",
                resource_id=str(claim.id),
                patient_id=str(claim.patient_id) if claim.patient_id else None,
                user_name=user_email,
                new_values=_summary_for_audit(claim),
                description=f"import: {entry.filename} VisitID {parsed_claim.visit_id}",
            )
        except Exception as exc:
            db.rollback()
            errors.append({
                "visit_id": parsed_claim.visit_id,
                "message": f"{type(exc).__name__}: {exc}",
            })

    log_action(
        db, "IMPORT", "charge_analysis_file",
        resource_id=session_id,
        user_name=user_email,
        description=(
            f"{entry.filename} — {claims_created} claims created, "
            f"{claims_skipped_existing} skipped, "
            f"{patients_created} patients created"
        ),
    )
    import_sessions.purge(session_id)

    return {
        "source_filename": entry.filename,
        "claims_created": claims_created,
        "claims_skipped_existing": claims_skipped_existing,
        "patients_created": patients_created,
        "patients_matched": patients_matched,
        "service_lines_created": service_lines_created,
        "errors": errors,
    }
