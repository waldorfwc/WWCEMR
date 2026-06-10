"""Enrich existing Active AR claims from a Charge Analysis XLS export.

Strategy:
  - Read the Charge Analysis file (one row per service line)
  - Aggregate by Visit ID into per-claim rollups
  - For each Visit ID, look up the matching ActiveClaim by claim_number
    (across ALL insurance_priority records — primary, secondary, tertiary
    all share the same claim_number)
  - Update the matched record(s) with the enriched fields
  - Skip Visit IDs not in the active_claims table (no creation)

The Charge Analysis file has 28 columns, with multiple "Unnamed: N" phantoms
that are dropped via pandas's named-column access.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from app.utils.dt import now_utc_naive
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.models.active_ar import ActiveClaim, ActiveClaimNote


# ─────────────────────────────────────────────────────────────────────
# Type-shape validators — defensive guards against PrimeSuite's occasional
# XLS export with shifted columns (right-side cells slip left/right by 1–2
# positions, contaminating fields with values from neighboring columns —
# e.g. `Patient: Sex` ('Female') showing up in `Provider: Rendering NPI`,
# or NPI digits showing up in `Charge: Void Indicator`).
#
# Pandas reads cells by their explicit position so we can't fix the layout.
# Instead we reject any value that doesn't look like the right TYPE for
# that field, and the field stays empty rather than carrying garbage.

NPI_RX = re.compile(r"^\d{10}$")
PROVIDER_TITLE_RX = re.compile(r"\b(MD|DO|NP|PA[-\s]?C|RN|MA|PHD)\b", re.IGNORECASE)


def _looks_like_npi(s: Optional[str]) -> bool:
    """Real NPIs are exactly 10 digits."""
    if not s:
        return False
    return bool(NPI_RX.fullmatch(s.strip()))


def _looks_like_provider_name(s: Optional[str]) -> bool:
    """Provider names look like 'Last, First [Title]'.
    Rejects pure-numeric (NPIs slipped in), single words ('Female', 'NO'),
    and bare titles. Comma is a strong signal."""
    if not s:
        return False
    s = s.strip()
    if len(s) < 3:
        return False
    if s.isdigit():
        return False
    return "," in s


def _looks_like_location(s: Optional[str]) -> bool:
    """Locations should NOT end with a provider credential.
    Catches the case where a provider name slips into the location column."""
    if not s:
        return False
    s = s.strip()
    if not s:
        return False
    # Bare provider name like "Cooke, Aryian MD" — has comma + title
    if "," in s and PROVIDER_TITLE_RX.search(s):
        return False
    return True


def _validate_npi_or_none(s: Optional[str]) -> Optional[str]:
    return s if _looks_like_npi(s) else None


def _validate_provider_or_none(s: Optional[str]) -> Optional[str]:
    return s if _looks_like_provider_name(s) else None


def _validate_location_or_none(s: Optional[str]) -> Optional[str]:
    return s if _looks_like_location(s) else None


REQUIRED_COLUMNS = [
    "Date: Service date of the Charge",
    "Patient: Patient ID",
    "Visit: VisitID",
    "Procedure: Code",
    "Insurance: Charge Primary Ins. Company",
]


@dataclass
class EnrichResult:
    total_rows: int
    visits_in_file: int
    matched_claim_records: int       # # of active_claim rows enriched
    unmatched_visits: int            # Visit IDs in file with no DB match
    unmatched_sample: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


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


def _first_nonnull(values: List[Any]) -> Optional[Any]:
    for v in values:
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            s = str(v).strip()
            if s:
                return v
    return None


def _join_unique(values: List[Any], sep: str = ", ") -> Optional[str]:
    seen: List[str] = []
    for v in values:
        s = _str(v)
        if s and s not in seen:
            seen.append(s)
    return sep.join(seen) if seen else None


def _decimal_or_none(v: Any) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(Decimal(str(v)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> Optional[int]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


import re as _re

_MODIFIER_RX = _re.compile(r"^[A-Z0-9]{2}$")


def _sanitize_modifier(v: Any) -> Optional[str]:
    """Validate a CPT modifier. Real modifiers are 2-char alphanumeric codes
    (LT, RT, 25, 95, GT, etc.), possibly comma/space-separated.

    When Greenway exports a row with no modifier, the next column's value
    (Charge Amount, a dollar figure) shifts left into the modifier slot.
    Reject those: anything that looks like money or a long string is not a
    real modifier — return None instead.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    # Numeric values are NEVER valid modifiers — they're shifted charges.
    if isinstance(v, (int, float)):
        return None
    s = str(v).strip()
    if not s or len(s) > 20:
        return None
    parts = _re.split(r"[\s,]+", s.upper())
    if all(_MODIFIER_RX.match(p) for p in parts if p):
        return ", ".join(parts)
    return None


def _sanitize_units(v: Any) -> Optional[int]:
    """Net Units must be a small integer (typically 1, sometimes 0-10).
    Strings or out-of-range values mean the column shifted — return None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    if 0 <= n <= 100:
        return n
    return None


def enrich_from_charge_analysis(
    db: Session, path: str, posted_by: Optional[str] = None,
) -> EnrichResult:
    df = pd.read_excel(path, sheet_name=0)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    # Drop voids
    void_col = "Charge: Void Indicator"
    if void_col in df.columns:
        df = df[df[void_col].astype(str).str.strip().str.upper() != "YES"]

    errors: List[str] = []
    total_rows = int(len(df))

    # Group by Visit ID — collapse line-level rows into per-visit rollups
    visit_rollups: Dict[str, Dict[str, Any]] = {}
    for _, raw in df.iterrows():
        visit_id = _str(raw.get("Visit: VisitID"))
        if not visit_id:
            continue
        vr = visit_rollups.setdefault(visit_id, {
            "patient_external_id": _str(raw.get("Patient: Patient ID")),
            "patient_dob": _parse_date(raw.get("Patient: Date Of Birth")),
            "dos": _parse_date(raw.get("Date: Service date of the Charge")),
            # Validate by shape — drop values that don't match expected
            # type. Defensive against shifted-column XLS exports.
            "service_location": _validate_location_or_none(
                _str(raw.get("Location: Service Location"))) or "",
            "billable_provider_npi": _validate_npi_or_none(
                _str(raw.get("Provider: Billable NPI"))) or "",
            "rendering_provider_name_full": _validate_provider_or_none(
                _str(raw.get("Provider: Rendering"))) or "",
            "rendering_provider_npi": _validate_npi_or_none(
                _str(raw.get("Provider: Rendering NPI"))) or "",
            "primary_company": _str(raw.get("Insurance: Charge Primary Ins. Company")),
            "primary_plan_detail": _str(raw.get("Insurance: Charge Primary Ins. Plan")),
            "primary_policy": _str(raw.get("Insurance: Charge Primary Policy Number")),
            "secondary_company": _str(raw.get("Insurance: Charge Secondary Ins. Company")),
            "secondary_plan": _str(raw.get("Insurance: Charge Secondary Ins. Plan")),
            "secondary_policy": _str(raw.get("Insurance: Charge Secondary Policy Number")),
            "_cpts": [],
            "_mods": [],
            "_dx": [],
            "_lines": [],   # full per-line detail
        })
        # Sanitize: when Modifier is blank in the export, Charge Amount
        # shifts left into the Modifier column. Reject non-modifier-looking
        # values (numeric / long strings) to avoid storing $$ as modifiers.
        sanitized_mod = _sanitize_modifier(raw.get("Procedure: Modifiers"))
        sanitized_units = _sanitize_units(raw.get("Charge: Net Units"))

        vr["_cpts"].append(raw.get("Procedure: Code"))
        vr["_mods"].append(sanitized_mod)
        vr["_dx"].append(raw.get("Diagnosis: Primary ICD-10 Code"))
        vr["_lines"].append({
            "cpt":                   _str(raw.get("Procedure: Code")),
            "modifiers":             sanitized_mod,
            "units":                 sanitized_units,
            "charge":                _decimal_or_none(raw.get("Charge: Charge Amount")),
            "gross_charge":          _decimal_or_none(raw.get("Charge: Gross Charges")),
            "fee_schedule_charge":   _decimal_or_none(raw.get("Procedure: Procedure Charge")),
            "dx":                    _str(raw.get("Diagnosis: Primary ICD-10 Code")),
        })

    # Bulk-load all active_claims that match ANY of the visit IDs in the file.
    visit_ids = list(visit_rollups.keys())
    matched_records = 0
    unmatched_visits = 0
    unmatched_sample: List[str] = []

    if not visit_ids:
        return EnrichResult(total_rows=total_rows, visits_in_file=0,
                            matched_claim_records=0, unmatched_visits=0,
                            errors=errors)

    # PrimeSuite issues different IDs in the Charge Analysis report
    # (Visit ID, e.g. 262xxx) vs the Unpaid Claims report (Claim ID, e.g.
    # 242xxx) for the same underlying claim. We can't match by ID — match
    # by (patient_external_id + dos) which IS stable across both reports.
    # Build the index from the file rollups: (pid, dos) -> visit_id
    db_claims: Dict[tuple, List[ActiveClaim]] = {}
    file_keys = {
        (vr["patient_external_id"], vr["dos"]): vid
        for vid, vr in visit_rollups.items()
        if vr.get("patient_external_id") and vr.get("dos")
    }
    if file_keys:
        # Fetch all active claims for the patient IDs in our file, then
        # filter in Python on (pid, dos)
        pids = {p for (p, _) in file_keys.keys()}
        for ac in db.query(ActiveClaim).filter(ActiveClaim.patient_external_id.in_(pids)).all():
            key = (ac.patient_external_id, ac.dos)
            db_claims.setdefault(key, []).append(ac)

    now = now_utc_naive()
    for visit_id, vr in visit_rollups.items():
        key = (vr.get("patient_external_id"), vr.get("dos"))
        records = db_claims.get(key, [])
        if not records:
            unmatched_visits += 1
            if len(unmatched_sample) < 25:
                unmatched_sample.append(visit_id)
            continue

        cpts = _join_unique(vr["_cpts"])
        mods = _join_unique(vr["_mods"])
        dxs = _join_unique(vr["_dx"])

        # Build per-line list (numbered 1..N, sorted as they appear)
        lines = [{**ln, "line": i + 1} for i, ln in enumerate(vr["_lines"])]
        lines_json = json.dumps(lines)

        # Some fields differ for secondary records (insurance company etc.).
        # We only override fields that come from the CHARGE side of the
        # claim (procedure / dx / providers / DOB / location). Insurance
        # fields stay as the unpaid-claims export wrote them, except we DO
        # capture secondary insurance from CA when missing on a primary
        # record.
        for ac in records:
            ac.procedure_codes = cpts
            ac.procedure_modifiers = mods
            ac.diagnosis_codes = dxs
            ac.service_lines_json = lines_json
            if vr["billable_provider_npi"]:
                ac.billable_provider_npi = vr["billable_provider_npi"]
            if vr["rendering_provider_name_full"]:
                ac.rendering_provider_name_full = vr["rendering_provider_name_full"]
            if vr["rendering_provider_npi"]:
                ac.rendering_provider_npi = vr["rendering_provider_npi"]
            if vr["service_location"]:
                ac.service_location = vr["service_location"]
            if vr["patient_dob"]:
                ac.patient_dob = vr["patient_dob"]
            if vr["primary_plan_detail"]:
                ac.primary_plan_detail = vr["primary_plan_detail"]
            # Capture secondary insurance only if not "No Secondary..." filler
            sec_co = vr["secondary_company"]
            if sec_co and "No Secondary" not in sec_co:
                ac.secondary_insurance_company = sec_co
                ac.secondary_plan_name = vr["secondary_plan"]
                ac.secondary_policy_number = vr["secondary_policy"]
            ac.enriched_at = now
            matched_records += 1

        # Optional: log enrichment as an activity note on the primary record
        primary = next((r for r in records if r.insurance_priority == "Primary"), records[0])
        db.add(ActiveClaimNote(
            active_claim_id=primary.id, user=posted_by or "system",
            action_type="enriched",
            note=(
                f"Enriched from Charge Analysis: "
                f"CPTs [{cpts or '—'}], "
                f"Dx [{dxs or '—'}], "
                f"Rendering: {vr['rendering_provider_name_full'] or '—'}"
            ),
        ))

    db.commit()
    return EnrichResult(
        total_rows=total_rows,
        visits_in_file=len(visit_ids),
        matched_claim_records=matched_records,
        unmatched_visits=unmatched_visits,
        unmatched_sample=unmatched_sample,
        errors=errors,
    )
