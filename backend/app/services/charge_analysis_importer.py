"""Charge Analysis importer — pure parser, no DB, no FastAPI.

Reads a PrimeSuite Charge Analysis .xls/.xlsx export and returns a
ChargeAnalysisImport dataclass. Does NOT perform patient matching or
deduplication — those happen at the endpoint / commit stage.
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
    "Patient: Patient ID",
    "Patient: First Name",
    "Patient: Last Name",
    "Date: Service date of the Charge",
    "Procedure: Code",
    "Provider: Rendering",
    "Provider: Rendering NPI",
    "Provider: Billable NPI",
    "Adjustment: Net Non-Primary Ins. Adjusted",
    "Adjustment: Net Patient/Other Adjusted",
    "Adjustment: Net Primary Ins. Adjusted",
    "Charge Balance: Patient",
    "Charge: Gross Charges",
    "Charge: Net Units",
    "Diagnosis: Primary ICD-10 Code",
    "Insurance: Charge Primary Ins. Company",
    "Insurance: Charge Primary Policy Number",
    "Insurance: Charge Secondary Ins. Company",
    "Insurance: Charge Secondary Policy Number",
    "Patient: Date Of Birth",
    "Patient: Phone Primary",
    "Patient: Address Line 1",
    "Patient: Address Line 2",
    "Patient: City",
    "Patient: State",
    "Patient: Zip Code",
    "Patient: Sex",
    "Payment: Net Patient/Other Applied",
    "Payment: Net Primary Ins. Applied",
    "Procedure: Modifiers",
    "Visit: VisitID",
    "Charge: Void Indicator",
]


@dataclass
class ParsedServiceLine:
    procedure_code: Optional[str]
    modifier_1: Optional[str]
    modifier_2: Optional[str]
    modifier_3: Optional[str]
    modifier_4: Optional[str]
    units: Decimal
    billed_amount: Decimal
    paid_amount: Decimal
    patient_responsibility: Decimal
    contractual_adjustment: Decimal
    other_adjustment: Decimal
    date_of_service_from: Optional[date]
    diagnosis_codes: List[str]


@dataclass
class ParsedClaim:
    visit_id: str
    patient_external_id: str
    patient_demographics: Dict[str, Any]
    date_of_service_from: Optional[date]
    payer_name: Optional[str]
    subscriber_id: Optional[str]
    secondary_payer_name: Optional[str]
    secondary_subscriber_id: Optional[str]
    rendering_provider_name: Optional[str]
    rendering_provider_npi: Optional[str]
    billing_provider_npi: Optional[str]
    billed_amount: Decimal
    paid_amount: Decimal
    patient_responsibility: Decimal
    contractual_adjustment: Decimal
    other_adjustment: Decimal
    service_lines: List[ParsedServiceLine] = field(default_factory=list)


@dataclass
class ParseIssue:
    severity: str  # "error" | "warning"
    row_index: int
    visit_id: Optional[str]
    message: str


@dataclass
class ChargeAnalysisImport:
    claims: List[ParsedClaim]
    skipped_voids: int
    skipped_non_clinical: int
    issues: List[ParseIssue]
    source_filename: str
    total_rows: int


# Sanity ceiling for any monetary cell. WWC's largest historical charge/payment
# tops out under $2K; values above this are column-shift artifacts (10-digit
# NPIs / claim-control numbers leaking into a money column).
_MONEY_SANITY_CEILING = Decimal("50000")


def _abs_decimal(v: Any) -> Decimal:
    """Coerce to Decimal and take absolute value. None/NaN → 0.
    Implausibly large values (>$50K) are clamped to 0 — they are always
    column-shift artifacts in this report family."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return Decimal("0")
    try:
        d = abs(Decimal(str(v)))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    if d > _MONEY_SANITY_CEILING:
        return Decimal("0")
    return d


def _decimal(v: Any) -> Decimal:
    """Coerce to Decimal, preserving sign. None/NaN → 0.
    Implausibly large values (|v|>$50K) are clamped to 0 — column-shift
    artifacts."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return Decimal("0")
    try:
        d = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    if abs(d) > _MONEY_SANITY_CEILING:
        return Decimal("0")
    return d


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    # pandas loves turning int-ish cells into "1124225222.0" floats
    if s.endswith(".0"):
        try:
            int_part = int(float(s))
            s = str(int_part)
        except ValueError:
            pass
    return s or None


def _parse_date(v: Any) -> Optional[date]:
    """Accept datetime, date, ISO string, or MM/DD/YYYY."""
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


def _pack_address(row: Dict[str, Any]) -> Optional[str]:
    line1 = _str_or_none(row.get("Patient: Address Line 1"))
    line2 = _str_or_none(row.get("Patient: Address Line 2"))
    city = _str_or_none(row.get("Patient: City"))
    state = _str_or_none(row.get("Patient: State"))
    zip_ = _str_or_none(row.get("Patient: Zip Code"))
    parts: List[str] = []
    if line1:
        parts.append(line1)
    if line2:
        parts.append(line2)
    city_state = ", ".join(p for p in [city, f"{state} {zip_}".strip() if (state or zip_) else ""] if p)
    if city_state:
        parts.append(city_state)
    return "\n".join(parts) or None


def _demographics_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "patient_id": _str_or_none(row.get("Patient: Patient ID")),
        "first_name": _str_or_none(row.get("Patient: First Name")),
        "last_name": _str_or_none(row.get("Patient: Last Name")),
        "date_of_birth": _parse_date(row.get("Patient: Date Of Birth")),
        "phone": _str_or_none(row.get("Patient: Phone Primary")),
        "address": _pack_address(row),
        # Captured but not persisted — Patient model has no `sex` column.
        "_sex": _str_or_none(row.get("Patient: Sex")),
    }


def _split_modifiers(v: Any) -> List[Optional[str]]:
    """Return [mod1, mod2, mod3, mod4] from a whitespace/comma separated string."""
    s = _str_or_none(v)
    if not s:
        return [None, None, None, None]
    parts = re.split(r"[\s,]+", s)
    parts = [p for p in parts if p]
    return (parts[:4] + [None] * 4)[:4]


def _build_service_line(row: Dict[str, Any], dx: Optional[str], dos: Optional[date]) -> ParsedServiceLine:
    mods = _split_modifiers(row.get("Procedure: Modifiers"))
    return ParsedServiceLine(
        procedure_code=_str_or_none(row.get("Procedure: Code")),
        modifier_1=mods[0],
        modifier_2=mods[1],
        modifier_3=mods[2],
        modifier_4=mods[3],
        units=_decimal(row.get("Charge: Net Units")),
        billed_amount=_decimal(row.get("Charge: Gross Charges")),
        paid_amount=_abs_decimal(row.get("Payment: Net Primary Ins. Applied")),
        patient_responsibility=_decimal(row.get("Charge Balance: Patient")),
        contractual_adjustment=_abs_decimal(row.get("Adjustment: Net Primary Ins. Adjusted")),
        other_adjustment=(
            _abs_decimal(row.get("Adjustment: Net Non-Primary Ins. Adjusted"))
            + _abs_decimal(row.get("Adjustment: Net Patient/Other Adjusted"))
        ),
        date_of_service_from=dos,
        diagnosis_codes=[dx] if dx else [],
    )


def parse(path: str) -> ChargeAnalysisImport:
    """Parse a Charge Analysis .xls/.xlsx file."""
    df = pd.read_excel(path, sheet_name=0)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    total_rows = int(len(df))
    issues: List[ParseIssue] = []
    skipped_voids = 0
    skipped_non_clinical = 0

    # Per-row parse into intermediate "(visit_id, row_dict, dx, dos, sl)" tuples,
    # then group by visit_id.
    groups: Dict[str, List[Dict[str, Any]]] = {}

    for row_index, raw in df.iterrows():
        row = raw.to_dict()

        # Void filter FIRST — skipped rows don't get further validation.
        void = _str_or_none(row.get("Charge: Void Indicator")) or ""
        if void.upper() == "YES":
            skipped_voids += 1
            continue

        # Finance-charge rows (patient-statement interest/fees) have no VisitID
        # and are not clinical claims — silent skip, like voids.
        procedure_code = _str_or_none(row.get("Procedure: Code"))
        if procedure_code == "F.Chg":
            skipped_non_clinical += 1
            continue

        visit_id = _str_or_none(row.get("Visit: VisitID"))
        if not visit_id:
            issues.append(ParseIssue("error", int(row_index), None,
                                     "missing Visit: VisitID — row dropped"))
            continue

        # Charge amount numeric check
        raw_charge = row.get("Charge: Gross Charges")
        if raw_charge is None or (isinstance(raw_charge, float) and pd.isna(raw_charge)):
            issues.append(ParseIssue("error", int(row_index), visit_id,
                                     "missing Charge: Gross Charges — row dropped"))
            continue
        try:
            Decimal(str(raw_charge))
        except (InvalidOperation, TypeError, ValueError):
            issues.append(ParseIssue("error", int(row_index), visit_id,
                                     f"non-numeric Charge: Gross Charges: {raw_charge!r} — row dropped"))
            continue

        # Modifier count warning
        raw_mod_str = _str_or_none(row.get("Procedure: Modifiers"))
        if raw_mod_str and len(re.split(r"[\s,]+", raw_mod_str)) > 4:
            issues.append(ParseIssue("warning", int(row_index), visit_id,
                                     f"more than 4 modifiers found in {raw_mod_str!r} — extras dropped"))

        # Negative charge warning
        if _decimal(raw_charge) < 0:
            issues.append(ParseIssue("warning", int(row_index), visit_id,
                                     f"negative Charge: Gross Charges: {raw_charge}"))

        groups.setdefault(visit_id, []).append({"__index__": int(row_index), **row})

    # Build one ParsedClaim per visit_id
    claims: List[ParsedClaim] = []
    for visit_id, rows in groups.items():
        first = rows[0]
        dos = _parse_date(first.get("Date: Service date of the Charge"))
        payer = _str_or_none(first.get("Insurance: Charge Primary Ins. Company"))
        sec_payer = _str_or_none(first.get("Insurance: Charge Secondary Ins. Company"))
        # Treat PrimeSuite's "No Secondary Insurance Company" placeholder as None
        if sec_payer and sec_payer.lower().startswith("no secondary"):
            sec_payer = None

        # Warn if payer differs across lines
        for r in rows[1:]:
            rp = _str_or_none(r.get("Insurance: Charge Primary Ins. Company"))
            if rp != payer:
                issues.append(ParseIssue("warning", r["__index__"], visit_id,
                                         f"payer name differs between lines; using first ({payer!r})"))
                break

        service_lines = []
        for r in rows:
            dx = _str_or_none(r.get("Diagnosis: Primary ICD-10 Code"))
            sl_dos = _parse_date(r.get("Date: Service date of the Charge"))
            service_lines.append(_build_service_line(r, dx, sl_dos))

        def _sum(attr: str) -> Decimal:
            return sum((getattr(sl, attr) for sl in service_lines), Decimal("0"))

        secondary_sub = _str_or_none(first.get("Insurance: Charge Secondary Policy Number"))

        claims.append(ParsedClaim(
            visit_id=visit_id,
            patient_external_id=_str_or_none(first.get("Patient: Patient ID")) or "",
            patient_demographics=_demographics_from_row(first),
            date_of_service_from=dos,
            payer_name=payer,
            subscriber_id=_str_or_none(first.get("Insurance: Charge Primary Policy Number")),
            secondary_payer_name=sec_payer,
            secondary_subscriber_id=secondary_sub if sec_payer else None,
            rendering_provider_name=_str_or_none(first.get("Provider: Rendering")),
            rendering_provider_npi=_str_or_none(first.get("Provider: Rendering NPI")),
            billing_provider_npi=_str_or_none(first.get("Provider: Billable NPI")),
            billed_amount=_sum("billed_amount"),
            paid_amount=_sum("paid_amount"),
            patient_responsibility=_sum("patient_responsibility"),
            contractual_adjustment=_sum("contractual_adjustment"),
            other_adjustment=_sum("other_adjustment"),
            service_lines=service_lines,
        ))

    return ChargeAnalysisImport(
        claims=claims,
        skipped_voids=skipped_voids,
        skipped_non_clinical=skipped_non_clinical,
        issues=issues,
        source_filename=os.path.basename(path),
        total_rows=total_rows,
    )
