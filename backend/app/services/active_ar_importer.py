"""Importer for the Greenway 'Unpaid Claims' export.

The XLS has 20 columns (4 phantom Unnamed) and is straightforward — no
column-shift gymnastics. Each row is one (claim_number, insurance_priority)
pair. Re-uploading the same report later updates balance/status without
overwriting locally-managed workflow state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from app.models.active_ar import ActiveClaim, ActiveClaimNote


REQUIRED_COLUMNS = [
    "Date of Service", "Patient ID", "Patient Name", "Care Provider",
    "Claim ID", "Claim State", "Claim Status", "Claim Amount",
    "Insurance Priority", "Payor ID", "Insurance Company",
    "Plan Name", "Policy Number", "Line Balance", "Insurance Balance",
    "Total Charges", "Practice Location",
]


@dataclass
class ImportResult:
    total_rows: int
    new_claims: int
    updated_claims: int
    closed_claims: int     # claims previously in DB but not in the new export
    unchanged: int
    errors: List[str]


def _str(v: Any) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
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
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


_PRIORITY_NORMALIZE = {
    "primary": "Primary", "p": "Primary",
    "secondary": "Secondary", "s": "Secondary",
    "tertiary": "Tertiary", "t": "Tertiary",
}


def _normalize_priority(v: Any) -> str:
    s = (_str(v) or "Primary").lower()
    return _PRIORITY_NORMALIZE.get(s, _str(v) or "Primary")


def import_unpaid_claims(
    db: Session, path: str, posted_by: Optional[str] = None,
    mark_missing_as_closed: bool = False,
) -> ImportResult:
    """Read an Unpaid Claims XLS and upsert into active_claims.

    `mark_missing_as_closed`: if True, any claim previously in the DB but
    NOT in this export is moved to workflow_state='closed'. Useful when the
    export is the authoritative current-AR snapshot.
    """
    df = pd.read_excel(path, sheet_name=0)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    # The export is one row per service line. Aggregate to one row per
    # (Claim ID, Insurance Priority) — sum money fields, take first value
    # for descriptive fields.
    money_cols = ["Claim Amount", "Line Balance", "Insurance Balance", "Total Charges"]
    first_cols = [c for c in df.columns if c not in money_cols + ["Claim ID", "Insurance Priority"]]
    agg_spec = {c: "sum" for c in money_cols}
    agg_spec.update({c: "first" for c in first_cols})
    df = df.groupby(["Claim ID", "Insurance Priority"], dropna=False, as_index=False).agg(agg_spec)

    errors: List[str] = []

    # Pre-load existing claims by (claim_number, priority)
    existing: dict[Tuple[str, str], ActiveClaim] = {}
    for c in db.query(ActiveClaim).all():
        existing[(c.claim_number, c.insurance_priority)] = c

    seen_keys: set[Tuple[str, str]] = set()
    new_count = updated_count = unchanged_count = 0
    now = datetime.utcnow()

    for idx, raw in df.iterrows():
        try:
            claim_number = _str(raw.get("Claim ID"))
            patient_external_id = _str(raw.get("Patient ID"))
            priority = _normalize_priority(raw.get("Insurance Priority"))
            if not claim_number or not patient_external_id:
                errors.append(f"row {idx}: missing claim_number or patient_id")
                continue

            key = (claim_number, priority)
            seen_keys.add(key)

            insurance_balance = _decimal(raw.get("Insurance Balance"))
            line_balance = _decimal(raw.get("Line Balance"))
            claim_amount = _decimal(raw.get("Claim Amount"))
            total_charges = _decimal(raw.get("Total Charges"))
            claim_status = _str(raw.get("Claim Status"))
            claim_state = _str(raw.get("Claim State"))

            existing_claim = existing.get(key)
            if existing_claim is None:
                # New
                dos_parsed = _parse_date(raw.get("Date of Service"))
                ins_co = _str(raw.get("Insurance Company"))
                # Precompute timely-filing deadline so the AR summary
                # endpoint can bucket by deadline via SQL.
                # (Fable cross-cutting audit #13.)
                from app.services.timely_filing import timely_filing_info
                tf = timely_filing_info(ins_co, dos_parsed)
                ac = ActiveClaim(
                    claim_number=claim_number,
                    patient_external_id=patient_external_id,
                    patient_name=_str(raw.get("Patient Name")),
                    dos=dos_parsed,
                    care_provider=_str(raw.get("Care Provider")),
                    claim_state=claim_state,
                    claim_status=claim_status,
                    claim_amount=claim_amount,
                    line_balance=line_balance,
                    insurance_balance=insurance_balance,
                    total_charges=total_charges,
                    insurance_priority=priority,
                    payor_id=_str(raw.get("Payor ID")),
                    insurance_company=ins_co,
                    plan_name=_str(raw.get("Plan Name")),
                    policy_number=_str(raw.get("Policy Number")),
                    practice_location=_str(raw.get("Practice Location")),
                    workflow_state="new",
                    last_seen_in_export_at=now,
                    imported_at=now,
                    tf_deadline_date=tf["tf_deadline_date"],
                    tf_days_allowed=tf["tf_days_allowed"],
                )
                db.add(ac)
                new_count += 1
            else:
                # Update — but never overwrite locally-managed fields
                # (workflow_state, assigned_to, paid_amount, notes)
                changed = False
                for fld, new_val in [
                    ("patient_name", _str(raw.get("Patient Name"))),
                    ("dos", _parse_date(raw.get("Date of Service"))),
                    ("care_provider", _str(raw.get("Care Provider"))),
                    ("claim_state", claim_state),
                    ("claim_status", claim_status),
                    ("claim_amount", claim_amount),
                    ("line_balance", line_balance),
                    ("insurance_balance", insurance_balance),
                    ("total_charges", total_charges),
                    ("payor_id", _str(raw.get("Payor ID"))),
                    ("insurance_company", _str(raw.get("Insurance Company"))),
                    ("plan_name", _str(raw.get("Plan Name"))),
                    ("policy_number", _str(raw.get("Policy Number"))),
                    ("practice_location", _str(raw.get("Practice Location"))),
                ]:
                    cur = getattr(existing_claim, fld)
                    if cur != new_val:
                        setattr(existing_claim, fld, new_val)
                        changed = True
                existing_claim.last_seen_in_export_at = now
                # If DOS or insurance changed, refresh tf_deadline_date.
                # Cheap to recompute on every row regardless, since the
                # classifier is a dict lookup + date math. (Audit #13.)
                from app.services.timely_filing import timely_filing_info
                tf = timely_filing_info(
                    existing_claim.insurance_company, existing_claim.dos)
                if (existing_claim.tf_deadline_date != tf["tf_deadline_date"]
                        or existing_claim.tf_days_allowed != tf["tf_days_allowed"]):
                    existing_claim.tf_deadline_date = tf["tf_deadline_date"]
                    existing_claim.tf_days_allowed = tf["tf_days_allowed"]
                    changed = True
                if changed:
                    updated_count += 1
                else:
                    unchanged_count += 1
        except Exception as exc:
            errors.append(f"row {idx}: {type(exc).__name__}: {exc}")

    closed_count = 0
    if mark_missing_as_closed:
        # Anything in the DB not seen in this export and still in an
        # actively-worked state → mark as closed (presumed paid externally
        # or otherwise resolved).
        for key, ac in existing.items():
            if key not in seen_keys and ac.workflow_state in (
                "new", "in_progress", "waiting_payer", "waiting_patient", "appealed",
            ):
                ac.workflow_state = "closed"
                db.add(ActiveClaimNote(
                    active_claim_id=ac.id, user=posted_by or "system",
                    action_type="status_changed",
                    note="Auto-closed: not present in latest unpaid-claims export.",
                ))
                closed_count += 1

    db.commit()

    return ImportResult(
        total_rows=int(len(df)),
        new_claims=new_count,
        updated_claims=updated_count,
        closed_claims=closed_count,
        unchanged=unchanged_count,
        errors=errors,
    )
