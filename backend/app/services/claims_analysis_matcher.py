"""Claims Analysis bootstrap — parser + matcher (pure, no DB writes here).

Reads the PrimeSuite Claims Analysis .xls export and produces
ClaimsAnalysisGroup records (one per unique Claim ID) plus match plans
against existing Claims in the DB. The router handles DB writes.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import pandas as pd


REQUIRED_COLUMNS = [
    "Patient ID", "Claim ID", "Date of Service", "Claim Amount",
    "Insurance Priority", "Claim Status", "Claim State",
]

VALID_PRIORITIES = {"primary", "secondary", "tertiary", "patient"}


@dataclass
class ClaimsAnalysisGroup:
    patient_external_id: str
    claim_id: str
    dos: Optional[date]
    total_amount: Decimal
    row_count: int
    insurance_priority: str             # "primary" | "secondary" | "tertiary" | "patient"
    internal_claim_id: str              # f"{claim_id}P{patient_external_id}"
    # Phase 2d enrichment
    claim_status_raw: Optional[str] = None
    claim_state: Optional[str] = None
    follow_up_date: Optional[date] = None
    follow_up_reason: Optional[str] = None
    last_submission_date: Optional[date] = None


@dataclass
class ParseIssue:
    severity: str                       # "error" | "warning"
    row_index: int
    claim_id: Optional[str]
    message: str


@dataclass
class ClaimsAnalysisImport:
    groups: List[ClaimsAnalysisGroup]
    source_filename: str
    total_rows: int
    skipped_rows: int
    issues: List[ParseIssue] = field(default_factory=list)


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    if s.endswith(".0"):
        try:
            s = str(int(float(s)))
        except ValueError:
            pass
    return s or None


def _decimal(v: Any) -> Decimal:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return Decimal("0")
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _parse_date(v: Any) -> Optional[date]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


from app.models.claim import ClaimStatus

CLAIMS_STATUS_MAP = {
    "paid in full": ClaimStatus.PAID,
    "paid partial": ClaimStatus.PARTIAL,
    "new/no eob": ClaimStatus.PENDING,
}


def map_claim_status(raw: Optional[str]) -> Optional["ClaimStatus"]:
    """Return ClaimStatus enum for a Claims Analysis status string, or None if unknown."""
    if not raw:
        return None
    return CLAIMS_STATUS_MAP.get(raw.strip().lower())


def parse(path: str) -> ClaimsAnalysisImport:
    df = pd.read_excel(path, sheet_name=0)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    total_rows = int(len(df))
    issues: List[ParseIssue] = []
    skipped_rows = 0

    # Row-level validation + grouping
    groups_map: Dict[str, List[Dict[str, Any]]] = {}
    for row_index, raw in df.iterrows():
        row = raw.to_dict()
        pid = _str_or_none(row.get("Patient ID"))
        cid = _str_or_none(row.get("Claim ID"))
        if not pid or not cid:
            skipped_rows += 1
            issues.append(ParseIssue(
                "error", int(row_index), cid,
                f"missing Patient ID or Claim ID — row dropped",
            ))
            continue
        groups_map.setdefault(cid, []).append({"__index__": int(row_index), **row, "__pid__": pid})

    groups: List[ClaimsAnalysisGroup] = []
    for cid, rows in groups_map.items():
        first = rows[0]
        pid = first["__pid__"]
        # Priority: first-row wins; warn if mixed
        priorities = {(_str_or_none(r.get("Insurance Priority")) or "").lower() for r in rows}
        if len(priorities) > 1:
            issues.append(ParseIssue(
                "warning", first["__index__"], cid,
                f"mixed Insurance Priority values across rows: {priorities}; using first",
            ))
        priority = (_str_or_none(first.get("Insurance Priority")) or "primary").lower()
        if priority not in VALID_PRIORITIES:
            issues.append(ParseIssue(
                "warning", first["__index__"], cid,
                f"unknown priority {priority!r}; defaulting to 'primary'",
            ))
            priority = "primary"

        total = sum((_decimal(r.get("Claim Amount")) for r in rows), Decimal("0"))
        dos = _parse_date(first.get("Date of Service"))
        claim_status_raw = _str_or_none(first.get("Claim Status"))
        # Warn on unmappable statuses (non-null but not in our map)
        if claim_status_raw and map_claim_status(claim_status_raw) is None:
            issues.append(ParseIssue(
                "warning", first["__index__"], cid,
                f"unknown Claim Status {claim_status_raw!r}; leaving existing status unchanged",
            ))
        claim_state = _str_or_none(first.get("Claim State"))
        follow_up_date = _parse_date(first.get("Follow-Up Date"))
        follow_up_reason = _str_or_none(first.get("Follow-Up Reason"))
        last_submission_date = _parse_date(first.get("Last Submission Date"))
        groups.append(ClaimsAnalysisGroup(
            patient_external_id=pid,
            claim_id=cid,
            dos=dos,
            total_amount=total,
            row_count=len(rows),
            insurance_priority=priority,
            internal_claim_id=f"{cid}P{pid}",
            claim_status_raw=claim_status_raw,
            claim_state=claim_state,
            follow_up_date=follow_up_date,
            follow_up_reason=follow_up_reason,
            last_submission_date=last_submission_date,
        ))

    return ClaimsAnalysisImport(
        groups=groups,
        source_filename=os.path.basename(path),
        total_rows=total_rows,
        skipped_rows=skipped_rows,
        issues=issues,
    )


from typing import Literal
from sqlalchemy.orm import Session
from app.models.claim import Claim, InsuranceOrder
from app.models.patient import Patient


MatchStatus = Literal[
    "will_patch", "will_create_secondary", "already_set",
    "no_patient", "no_claim", "ambiguous", "conflict",
]


@dataclass
class MatchResult:
    group: ClaimsAnalysisGroup
    status: MatchStatus
    matched_claim_id: Optional[str] = None   # our internal UUID as str
    conflict_existing_value: Optional[str] = None


_PRIORITY_TO_ORDER = {
    "primary": InsuranceOrder.PRIMARY,
    "secondary": InsuranceOrder.SECONDARY,
    "tertiary": InsuranceOrder.TERTIARY,
    "patient": InsuranceOrder.PATIENT,
}


def _candidate_claims(db: Session, patient_id: str, dos: Optional[date],
                      billed: Decimal, order: InsuranceOrder) -> List[Claim]:
    q = db.query(Claim).filter(
        Claim.patient_id == patient_id,
        Claim.insurance_order == order,
        Claim.billed_amount == billed,
    )
    if dos is not None:
        q = q.filter(Claim.date_of_service_from == dos)
    return q.all()


def match_groups(db: Session, groups: List[ClaimsAnalysisGroup]) -> List[MatchResult]:
    results: List[MatchResult] = []
    for g in groups:
        patient = db.query(Patient).filter(Patient.patient_id == g.patient_external_id).first()
        if patient is None:
            results.append(MatchResult(group=g, status="no_patient"))
            continue

        order = _PRIORITY_TO_ORDER.get(g.insurance_priority, InsuranceOrder.PRIMARY)

        if g.insurance_priority == "primary":
            candidates = _candidate_claims(db, patient.id, g.dos, g.total_amount, order)
            if not candidates:
                results.append(MatchResult(group=g, status="no_claim"))
                continue
            if len(candidates) > 1:
                results.append(MatchResult(group=g, status="ambiguous"))
                continue
            claim = candidates[0]
            if claim.patient_control_number is None:
                results.append(MatchResult(group=g, status="will_patch",
                                           matched_claim_id=str(claim.id)))
            elif claim.patient_control_number == g.internal_claim_id:
                results.append(MatchResult(group=g, status="already_set",
                                           matched_claim_id=str(claim.id)))
            else:
                results.append(MatchResult(group=g, status="conflict",
                                           matched_claim_id=str(claim.id),
                                           conflict_existing_value=claim.patient_control_number))
            continue

        # Secondary / tertiary / patient
        existing = _candidate_claims(db, patient.id, g.dos, g.total_amount, order)
        if existing:
            # Existing higher-COB claim. Check PCN state.
            if len(existing) > 1:
                results.append(MatchResult(group=g, status="ambiguous"))
                continue
            claim = existing[0]
            if claim.patient_control_number is None:
                results.append(MatchResult(group=g, status="will_patch",
                                           matched_claim_id=str(claim.id)))
            elif claim.patient_control_number == g.internal_claim_id:
                results.append(MatchResult(group=g, status="already_set",
                                           matched_claim_id=str(claim.id)))
            else:
                results.append(MatchResult(group=g, status="conflict",
                                           matched_claim_id=str(claim.id),
                                           conflict_existing_value=claim.patient_control_number))
            continue

        # No existing higher-COB claim — we need a primary to copy from
        primary = _candidate_claims(db, patient.id, g.dos, g.total_amount, InsuranceOrder.PRIMARY)
        if not primary:
            results.append(MatchResult(group=g, status="no_claim"))
            continue
        if len(primary) > 1:
            results.append(MatchResult(group=g, status="ambiguous"))
            continue
        results.append(MatchResult(group=g, status="will_create_secondary",
                                   matched_claim_id=str(primary[0].id)))

    return results
