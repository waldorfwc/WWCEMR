"""Cross-import drift detection.

Workflow:

  1. After parsing a report into a DataFrame, call `compute_fingerprints(df, ...)`
     to get a list of (natural_key, value_hash) tuples.
  2. Call `check_drift(db, report_type, period_start, period_end, fingerprints)`
     to compare against the most recent prior import for that period.
  3. Pass the resulting `DriftReport` back in the API response so the user
     can see what changed before they commit.

The drift check is *advisory* — it doesn't block imports; it surfaces
discrepancies so a human can decide whether to proceed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any, Iterable, Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.models.import_audit import ImportAuditLog, ImportRowFingerprint


# ----------------------------- fingerprinting ----------------------------- #

def _canon(v: Any) -> str:
    """Stable string form for hashing — strips whitespace, normalizes NaN/None."""
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    s = str(v).strip()
    if s.endswith(".0"):
        # pandas often reads ints as floats; "187122.0" should hash same as "187122"
        try:
            s = str(int(float(s)))
        except ValueError:
            pass
    return s


def compute_fingerprints(
    df: pd.DataFrame,
    key_columns: list[str],
    value_columns: list[str],
) -> list[tuple[str, str]]:
    """Return [(natural_key, value_hash), ...] for every row.

    natural_key is the concatenation of canonical key-column values,
    pipe-separated. value_hash is a 16-hex-char SHA-256 prefix of the
    canonical value-column concatenation.
    """
    out: list[tuple[str, str]] = []
    for _, row in df.iterrows():
        nk = "|".join(_canon(row.get(c)) for c in key_columns)
        vstr = "|".join(_canon(row.get(c)) for c in value_columns)
        vh = hashlib.sha256(vstr.encode("utf-8")).hexdigest()[:16]
        out.append((nk, vh))
    return out


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------ drift report ------------------------------ #

@dataclass
class ChangedRow:
    natural_key: str
    prior_value_hash: str
    new_value_hash: str


@dataclass
class DriftReport:
    has_prior_import: bool
    prior_import_id: Optional[str] = None
    prior_imported_at: Optional[str] = None
    prior_filename: Optional[str] = None

    rows_added: int = 0       # natural_keys in new but not prior
    rows_removed: int = 0     # natural_keys in prior but not new
    rows_changed: int = 0     # same key, different value_hash

    # Sample lists for the UI (capped to keep payload small)
    sample_added: list[str] = field(default_factory=list)
    sample_removed: list[str] = field(default_factory=list)
    sample_changed: list[ChangedRow] = field(default_factory=list)

    # Free-form notes about whether drift is *expected* for this report type
    # (e.g. Claims Analysis workflow state evolves; Transaction Detail closed
    # periods should never drift).
    interpretation: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


# Per-report-type expectations for drift interpretation.
# `mutable` = whether row values for the same natural_key are expected to
# change between pulls (workflow state, follow-up dates, etc.).
REPORT_PROFILES = {
    "transaction_detail": {
        "mutable": False,
        "label": "Transaction Detail",
        "notes": [
            "Transaction Detail is append-only — closed periods should show ZERO drift.",
            "Any changed/removed rows in a closed period indicate backdating or data corruption.",
        ],
    },
    "claims_analysis": {
        "mutable": True,
        "label": "Claims Analysis",
        "notes": [
            "Workflow state (Claim Status, Follow-Up Date, Last Submission Date) evolves over time — changed rows are EXPECTED.",
            "Removed rows are still suspicious (a claim shouldn't disappear).",
        ],
    },
    "charge_analysis": {
        "mutable": False,
        "label": "Charge Analysis",
        "notes": [
            "Charges are write-once after posting — same-period drift indicates a void, correction, or backdating.",
            "Changed rows in a closed period warrant review.",
        ],
    },
}


def check_drift(
    db: Session,
    report_type: str,
    period_start: Optional[date],
    period_end: Optional[date],
    fingerprints: list[tuple[str, str]],
) -> DriftReport:
    """Compare new fingerprints against the most-recent prior import for the
    same (report_type, period). Returns a DriftReport describing what changed."""
    profile = REPORT_PROFILES.get(report_type, {"mutable": False, "notes": []})

    prior = (
        db.query(ImportAuditLog)
        .filter(
            ImportAuditLog.report_type == report_type,
            ImportAuditLog.period_start == period_start,
            ImportAuditLog.period_end == period_end,
        )
        .order_by(ImportAuditLog.imported_at.desc())
        .first()
    )

    if prior is None:
        return DriftReport(
            has_prior_import=False,
            interpretation=["No prior import found for this period — first ingestion."],
        )

    # Build prior fingerprint map: {natural_key: value_hash}
    prior_map: dict[str, str] = {}
    for fp in db.query(ImportRowFingerprint).filter(
        ImportRowFingerprint.audit_log_id == prior.id
    ).all():
        prior_map[fp.natural_key] = fp.value_hash

    new_map: dict[str, str] = {nk: vh for nk, vh in fingerprints}

    added_keys: list[str] = []
    changed: list[ChangedRow] = []
    for nk, vh in new_map.items():
        if nk not in prior_map:
            added_keys.append(nk)
        elif prior_map[nk] != vh:
            changed.append(ChangedRow(
                natural_key=nk,
                prior_value_hash=prior_map[nk],
                new_value_hash=vh,
            ))

    removed_keys: list[str] = [nk for nk in prior_map if nk not in new_map]

    interpretation: list[str] = list(profile.get("notes", []))
    if added_keys:
        interpretation.append(
            f"{len(added_keys)} row(s) appeared that weren't in the previous import — "
            "expected for active periods, suspicious for closed ones."
        )
    if removed_keys:
        interpretation.append(
            f"⚠️  {len(removed_keys)} row(s) from the previous import are MISSING in this pull. "
            "Investigate before committing."
        )
    if changed:
        if profile.get("mutable"):
            interpretation.append(
                f"{len(changed)} row(s) have updated values — normal for {profile.get('label')} "
                "(workflow state evolves)."
            )
        else:
            interpretation.append(
                f"⚠️  {len(changed)} row(s) have CHANGED values — Transaction Detail / "
                "Charge Analysis should not change retroactively. Investigate."
            )

    return DriftReport(
        has_prior_import=True,
        prior_import_id=str(prior.id),
        prior_imported_at=prior.imported_at.isoformat() if prior.imported_at else None,
        prior_filename=prior.source_filename,
        rows_added=len(added_keys),
        rows_removed=len(removed_keys),
        rows_changed=len(changed),
        sample_added=added_keys[:20],
        sample_removed=removed_keys[:20],
        sample_changed=changed[:20],
        interpretation=interpretation,
    )


# -------------------------- audit-log persistence -------------------------- #

def write_audit_log(
    db: Session,
    *,
    report_type: str,
    period_start: Optional[date],
    period_end: Optional[date],
    source_filename: str,
    file_path: str,
    fingerprints: list[tuple[str, str]],
    drift_report: DriftReport,
    row_count: int,
    total_amount: Optional[float] = None,
    secondary_total: Optional[float] = None,
    imported_by: Optional[str] = None,
) -> ImportAuditLog:
    """Persist the audit log + fingerprints. Caller is responsible for db.commit()
    AFTER the actual data import succeeds."""
    log = ImportAuditLog(
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        source_filename=source_filename,
        file_sha256=file_sha256(file_path),
        row_count=row_count,
        total_amount=total_amount,
        secondary_total=secondary_total,
        rows_added=drift_report.rows_added,
        rows_removed=drift_report.rows_removed,
        rows_changed=drift_report.rows_changed,
        drift_report_json=drift_report.to_json(),
        imported_by=imported_by,
    )
    db.add(log)
    db.flush()  # populate log.id for the children

    for nk, vh in fingerprints:
        db.add(ImportRowFingerprint(
            audit_log_id=log.id,
            natural_key=nk,
            value_hash=vh,
        ))
    return log


# --------------------- per-report key/value definitions --------------------- #
# These tell `compute_fingerprints` which columns count as the natural key
# (identity) vs. the value (what we want to detect changes in).

KEYS_AND_VALUES = {
    "transaction_detail": {
        "key_columns": [
            "Patient: Patient ID",
            "Transaction: Visit ID",
            "Transaction: Charge Ticket Number",
            "Date: Posting Date",
            "Date: Date of Service",
            "Transaction: Procedure Code",
            "Transaction: Type",
        ],
        "value_columns": [
            "Transaction: Amount - Net Charges",
            "Transaction: Amount - Net Adjustments",
            "Transaction: Amount - Net Payments",
            "Transaction: Amount - Charge Voids",
            "Transaction: Amount - Adjustment Voids",
            "Transaction: Amount - Payment Voids",
            "Transaction: Applied To",
            "Transaction: Adjustment Type",
            "Transaction: Adjustment Sub-Type",
            "Transaction: Void Indicator",
        ],
    },
    "claims_analysis": {
        "key_columns": [
            "Patient ID",
            "Claim ID",
        ],
        "value_columns": [
            "Claim Status",
            "Claim State",
            "Claim Amount",
            "Insurance Priority",
            "Follow-Up Date",
            "Follow-Up Reason",
            "Last Submission Date",
            "Insurance Paid Amount",
            "Patient Balance",
        ],
    },
    "charge_analysis": {
        "key_columns": [
            "Patient: Patient ID",
            "Visit: VisitID",
            "Charge: Charge Ticket ID",
            "Date: Service date of the Charge",
            "Procedure: Code",
            "Procedure: Modifiers",
        ],
        "value_columns": [
            "Charge: Gross Charges",
            "Charge: Net Charges",
            "Charge: Charge Amount",
            "Adjustment: Net Primary Ins. Adjusted",
            "Adjustment: Net Patient/Other Adjusted",
            "Payment: Net Primary Ins. Applied",
            "Payment: Net Patient/Other Applied",
            "Charge: Void Indicator",
        ],
    },
}
