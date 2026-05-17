"""Transaction Detail importer — parses TD files into Payment + Adjustment
records to be linked to existing Claims.

Architecture decisions (from Q4 2025 cross-validation):

* TD CHG rows are SKIPPED — Charge Analysis is the truth source for charges.
  TD's money columns on CHG rows are unreliable due to multi-blank-zone
  shifts the greedy walker can't fix.
* TD PMT rows → Payment records, classified by Source:
    Source = "Insurance" → INSURANCE_PAYMENT
    Source = "Patient"   → PATIENT_PAYMENT (with method)
    Source = "Other"     → ADJUSTMENT (catch-all)
* TD C-ADJ / D-ADJ → ClaimAdjustment records
* V-* (voids) and SLT-/MTC- (transfers) — not yet handled, recorded as
  unmatched for v2.

Linking strategy: Visit ID + Patient ID → Claim. If Visit ID is missing
or no claim exists, the row goes into `unmatched` for user review.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional

import pandas as pd

from app.services.transaction_detail_fixer import (
    NCOLS as TD_NCOLS, fix_file as fix_td_file,
)


@dataclass
class TDPaymentRecord:
    """One payment row to insert. Source determines payment_type."""
    patient_external_id: str
    visit_id: Optional[str]
    posting_date: Optional[date]
    payment_date: Optional[date]    # we use Original Posting Date when distinct, else Posting Date
    amount: Decimal                  # positive number
    source: str                      # "Insurance" | "Patient" | "Other"
    method: Optional[str]            # "Credit Card" | "EFT" | "Check" | ...
    payer_name: Optional[str]        # extracted from Additional Info or blank
    user: Optional[str]              # who posted
    raw_row_index: int


@dataclass
class TDAdjustmentRecord:
    """One adjustment row (C-ADJ, D-ADJ) to insert."""
    patient_external_id: str
    visit_id: Optional[str]
    posting_date: Optional[date]
    amount: Decimal                  # signed; adjustments are typically negative
    adjustment_type: Optional[str]   # "Contractual" / "In-House" / etc.
    adjustment_sub_type: Optional[str]
    user: Optional[str]
    raw_row_index: int


@dataclass
class TDImport:
    """Result of parsing a Transaction Detail file."""
    source_filename: str
    total_rows: int
    payments: List[TDPaymentRecord]
    adjustments: List[TDAdjustmentRecord]
    skipped_chg: int                 # CHG rows (use CA instead)
    skipped_voids: int
    skipped_transfers: int           # SLT-*, MTC-*
    skipped_other: int
    unmatched: List[dict]            # rows we couldn't resolve to a Visit
    type_counts: dict


# ---------- helpers ----------

def _str_or_none(v: Any) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if s.endswith(".0"):
        try:
            s = str(int(float(s)))
        except ValueError:
            pass
    return s or None


# Sanity ceiling — values above this in a money cell are column-shift
# artifacts (NPIs / claim-control numbers). WWC's largest single payment
# tops out under $2K, charge under $50K.
_MONEY_SANITY_CEILING = Decimal("50000")


def _decimal(v: Any) -> Decimal:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return Decimal("0")
    try:
        d = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    if abs(d) > _MONEY_SANITY_CEILING:
        return Decimal("0")
    return d


def _parse_date(v: Any) -> Optional[date]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _extract_payer(additional_info: Optional[str]) -> Optional[str]:
    """Extract a payer reference (check#/trace#/EFT trace) from the
    'Additional Info' field. Post-fixer this often contains just the trace
    number; pre-fixer it can be the full 'Source; Method; [Amt]; Trace' form."""
    if not additional_info:
        return None
    s = additional_info.strip()
    parts = [p.strip() for p in s.split(";") if p.strip()]
    if len(parts) >= 4:
        return parts[3]
    return s if len(s) <= 50 else None


# Method → likely source. Insurance pays via EFT/ACH/Wire today; the few
# remaining paper checks at WWC are overwhelmingly patient-issued (statement
# payments). Older convention defaulted Check → Insurance, which silently
# misclassified ~thousands of patient checks. Always prefer the explicit
# Source field when present; only fall back to Method.
_METHOD_TO_SOURCE = {
    "EFT": "Insurance",
    "ACH": "Insurance",
    "WIRE": "Insurance",
    "ELECTRONIC": "Insurance",
    "CHECK": "Patient",  # paper checks are patient statement payments
    "CREDIT CARD": "Patient",
    "DEBIT CARD": "Patient",
    "CASH": "Patient",
    "MONEY ORDER": "Patient",
    "CC": "Patient",
}


def _infer_source(explicit_source: Optional[str], method: Optional[str]) -> str:
    """Resolve PMT source. Prefer explicit Source value; fall back to Method."""
    if explicit_source:
        s = explicit_source.strip()
        if s and s.upper() not in ("0", "NAN", "NONE"):
            return s.title()
    if method:
        m = method.strip().upper()
        if m in _METHOD_TO_SOURCE:
            return _METHOD_TO_SOURCE[m]
        # If method looks like a trace number (digits, no spaces), the row is
        # shifted and we can't determine the source.
        if re.match(r"^[\dA-Z]{8,}$", m, re.IGNORECASE):
            return "Unknown"
    return "Unknown"


# ---------- main parser ----------

def parse(path: str) -> TDImport:
    """Read a Transaction Detail file (raw or pre-fixed) and produce an
    importable record set. Always runs the autofix-shifts pre-processor."""
    fixed_path = path + ".tdfix.xlsx"
    fix_td_file(path, fixed_path)
    df = pd.read_excel(fixed_path, sheet_name=0)
    try:
        os.remove(fixed_path)
    except OSError:
        pass

    payments: List[TDPaymentRecord] = []
    adjustments: List[TDAdjustmentRecord] = []
    unmatched: List[dict] = []
    skipped_chg = skipped_voids = skipped_transfers = skipped_other = 0
    type_counts: dict[str, int] = {}

    for idx, row in df.iterrows():
        ttype = _str_or_none(row.get("Transaction: Type"))
        type_counts[ttype or "(blank)"] = type_counts.get(ttype or "(blank)", 0) + 1

        # Skip CHG — use Charge Analysis instead.
        if ttype == "CHG":
            skipped_chg += 1
            continue
        if ttype and ttype.startswith("V-"):
            skipped_voids += 1
            continue
        if ttype in ("SLT-TO", "SLT-FROM", "MTC-TO", "MTC-FROM"):
            skipped_transfers += 1
            continue
        if ttype not in ("PMT", "C-ADJ", "D-ADJ"):
            skipped_other += 1
            continue

        pat_external = _str_or_none(row.get("Patient: Patient ID"))
        visit_id = _str_or_none(row.get("Transaction: Visit ID"))
        posting_date = _parse_date(row.get("Date: Posting Date"))
        original_post = _parse_date(row.get("Date: Original Posting Date"))
        net_payments = _decimal(row.get("Transaction: Amount - Net Payments"))
        net_adjustments = _decimal(row.get("Transaction: Amount - Net Adjustments"))
        user = _str_or_none(row.get("Transaction: User"))

        if not pat_external:
            unmatched.append({
                "row_index": int(idx),
                "type": ttype,
                "reason": "missing Patient ID",
            })
            continue

        if ttype == "PMT":
            explicit_source = _str_or_none(row.get("Transaction: Payment/Adjustment Source"))
            method = _str_or_none(row.get("Transaction: Payment Method"))
            additional_info = _str_or_none(row.get("Transaction: Payment/Adjustment Additional Info"))
            source = _infer_source(explicit_source, method)
            payer = _extract_payer(additional_info)
            amount = abs(net_payments) if net_payments else Decimal("0")
            if amount == 0:
                skipped_other += 1
                continue
            payments.append(TDPaymentRecord(
                patient_external_id=pat_external,
                visit_id=visit_id,
                posting_date=posting_date,
                payment_date=original_post or posting_date,
                amount=amount,
                source=source,
                method=method,
                payer_name=payer,
                user=user,
                raw_row_index=int(idx),
            ))
            continue

        if ttype in ("C-ADJ", "D-ADJ"):
            adj_type = _str_or_none(row.get("Transaction: Adjustment Type"))
            adj_sub = _str_or_none(row.get("Transaction: Adjustment Sub-Type"))
            amount = net_adjustments  # signed; usually negative
            if amount == 0:
                skipped_other += 1
                continue
            adjustments.append(TDAdjustmentRecord(
                patient_external_id=pat_external,
                visit_id=visit_id,
                posting_date=posting_date,
                amount=amount,
                adjustment_type=adj_type,
                adjustment_sub_type=adj_sub,
                user=user,
                raw_row_index=int(idx),
            ))
            continue

    return TDImport(
        source_filename=os.path.basename(path),
        total_rows=int(len(df)),
        payments=payments,
        adjustments=adjustments,
        skipped_chg=skipped_chg,
        skipped_voids=skipped_voids,
        skipped_transfers=skipped_transfers,
        skipped_other=skipped_other,
        unmatched=unmatched,
        type_counts=type_counts,
    )
