"""
Greenway PrimeSuite EMR export mapper.

Handles the common CSV/Excel export formats from PrimeSuite:
  - AR Aging Report
  - Charge Capture / Superbill
  - Payment Posting / Transaction Report
  - Claim Status Report
  - Patient Demographics

PrimeSuite column names vary by site configuration and version.
We detect format by looking for signature column sets.
"""

from typing import List, Dict, Optional, Tuple
import re


# ── Column fingerprints ────────────────────────────────────────────────────────

# Each entry: (format_name, score_columns, required_columns)
PRIMESUITE_FORMATS = [
    (
        "ar_aging",
        ["0_30", "31_60", "61_90", "91_120", "over_120", "current", "aging", "bucket"],
        ["patient", "total"],
    ),
    (
        "charge_capture",
        ["cpt", "procedure", "charge", "units", "diagnosis", "icd", "dos", "date_of_service"],
        ["charge", "patient"],
    ),
    (
        "payment_posting",
        ["payment", "check", "check_number", "remit", "posted", "deposit"],
        ["amount", "patient"],
    ),
    (
        "claim_status",
        ["claim", "billed", "paid", "balance", "status", "payer", "insurance"],
        ["claim", "billed"],
    ),
    (
        "patient_demographics",
        ["mrn", "date_of_birth", "dob", "insurance_id", "member_id", "subscriber"],
        ["patient"],
    ),
]


def detect_primesuite_format(columns: List[str]) -> Optional[str]:
    """
    Detect which PrimeSuite export format a column list matches.
    Returns format name or None.
    """
    normalized = [_norm(c) for c in columns]
    col_str = " ".join(normalized)

    best_format = None
    best_score = 0

    for fmt_name, score_cols, required_cols in PRIMESUITE_FORMATS:
        # Must have at least one required column
        has_required = any(
            any(req in nc for nc in normalized)
            for req in required_cols
        )
        if not has_required:
            continue

        score = sum(
            1 for sc in score_cols
            if any(sc in nc for nc in normalized) or sc in col_str
        )
        if score > best_score:
            best_score = score
            best_format = fmt_name

    return best_format if best_score >= 2 else None


def is_primesuite_file(columns: List[str]) -> bool:
    """Quick check: does this look like a PrimeSuite export?"""
    return detect_primesuite_format(columns) is not None


def map_ar_aging_row(row: Dict) -> Dict:
    """
    Normalize a PrimeSuite AR Aging row to standard A/R fields.
    Returns a dict with consistent keys regardless of PrimeSuite column naming.
    """
    r = {k: v for k, v in row.items()}

    return {
        "patient_name": _find(r, ["patient_name", "patient", "name", "last_name_first_name"]),
        "account_number": _find(r, ["account", "account_number", "account_no", "acct", "mrn", "chart"]),
        "insurance_1": _find(r, ["primary_ins", "insurance_1", "ins_1", "primary", "ins1", "primary_insurance"]),
        "insurance_2": _find(r, ["secondary_ins", "insurance_2", "ins_2", "secondary", "ins2"]),
        "insurance_3": _find(r, ["tertiary_ins", "insurance_3", "ins_3", "tertiary", "ins3"]),
        "bucket_0_30": _money(r, ["0_30", "current_0_30", "0_30_days", "cur", "current"]),
        "bucket_31_60": _money(r, ["31_60", "31_60_days", "30_60"]),
        "bucket_61_90": _money(r, ["61_90", "61_90_days", "60_90"]),
        "bucket_91_120": _money(r, ["91_120", "91_120_days", "90_120"]),
        "bucket_120_plus": _money(r, ["over_120", "120_plus", "121_plus", "120_", "121_"]),
        "total_balance": _money(r, ["total", "total_balance", "balance", "total_ar"]),
        "provider": _find(r, ["provider", "rendering_provider", "physician", "doctor", "attending"]),
        "location": _find(r, ["location", "office", "facility", "site"]),
        "last_payment_date": _find(r, ["last_payment", "last_payment_date", "last_pmt_date"]),
        "last_payment_amount": _money(r, ["last_payment_amount", "last_pmt_amt"]),
        "_source_format": "primesuite_ar_aging",
    }


def map_charge_capture_row(row: Dict) -> Dict:
    """Normalize a PrimeSuite Charge Capture / Superbill row."""
    r = {k: v for k, v in row.items()}
    return {
        "patient_name": _find(r, ["patient_name", "patient", "name"]),
        "account_number": _find(r, ["account", "account_number", "account_no", "acct", "mrn"]),
        "date_of_service": _find(r, ["date_of_service", "dos", "service_date", "date"]),
        "procedure_code": _find(r, ["cpt", "cpt_code", "procedure_code", "procedure", "proc_code"]),
        "modifier": _find(r, ["modifier", "mod", "modifier_1"]),
        "units": _float(r, ["units", "qty", "quantity"]),
        "charge_amount": _money(r, ["charge", "charge_amount", "fee", "amount_charged"]),
        "diagnosis_1": _find(r, ["icd", "icd_1", "diagnosis", "dx", "dx_1", "diagnosis_code"]),
        "diagnosis_2": _find(r, ["icd_2", "dx_2", "diagnosis_2"]),
        "provider": _find(r, ["provider", "rendering_provider", "physician"]),
        "insurance_1": _find(r, ["primary_ins", "insurance", "insurance_1", "ins_1", "payer"]),
        "facility": _find(r, ["facility", "location", "office", "place_of_service", "pos"]),
        "_source_format": "primesuite_charge_capture",
    }


def map_payment_row(row: Dict) -> Dict:
    """Normalize a PrimeSuite Payment Posting row."""
    r = {k: v for k, v in row.items()}
    return {
        "patient_name": _find(r, ["patient_name", "patient", "name"]),
        "account_number": _find(r, ["account", "account_number", "mrn"]),
        "payment_date": _find(r, ["payment_date", "post_date", "date", "check_date"]),
        "check_number": _find(r, ["check_number", "check_no", "check", "ck_number", "ref"]),
        "payer": _find(r, ["payer", "insurance", "ins", "from", "source"]),
        "payment_amount": _money(r, ["amount", "payment", "payment_amount", "paid"]),
        "claim_number": _find(r, ["claim", "claim_number", "claim_no", "claim_id"]),
        "date_of_service": _find(r, ["dos", "date_of_service", "service_date"]),
        "procedure_code": _find(r, ["cpt", "procedure_code", "procedure"]),
        "applied_to": _find(r, ["applied_to", "applied", "visit"]),
        "_source_format": "primesuite_payment",
    }


def map_claim_status_row(row: Dict) -> Dict:
    """Normalize a PrimeSuite Claim Status row."""
    r = {k: v for k, v in row.items()}
    return {
        "claim_number": _find(r, ["claim_number", "claim_no", "claim_id", "claim"]),
        "patient_name": _find(r, ["patient_name", "patient", "name"]),
        "account_number": _find(r, ["account", "account_number", "mrn"]),
        "date_of_service": _find(r, ["dos", "date_of_service", "service_date"]),
        "payer": _find(r, ["payer", "insurance", "ins_name", "carrier"]),
        "payer_id": _find(r, ["payer_id", "ins_id", "carrier_id"]),
        "status": _find(r, ["status", "claim_status", "ins_status"]),
        "billed_amount": _money(r, ["billed", "billed_amount", "charge", "submitted"]),
        "paid_amount": _money(r, ["paid", "paid_amount", "payment", "ins_paid"]),
        "adjustment_amount": _money(r, ["adjustment", "adj", "contractual", "write_off"]),
        "patient_responsibility": _money(r, ["patient_responsibility", "patient_portion", "pt_resp", "copay"]),
        "balance": _money(r, ["balance", "outstanding", "remaining"]),
        "provider": _find(r, ["provider", "rendering_provider", "physician"]),
        "_source_format": "primesuite_claim_status",
    }


def normalize_primesuite_rows(rows: List[Dict], fmt: Optional[str] = None) -> Tuple[str, List[Dict]]:
    """
    Auto-detect format and normalize all rows.
    Returns (detected_format, normalized_rows).
    """
    if not rows:
        return ("unknown", [])

    if fmt is None and rows:
        fmt = detect_primesuite_format(list(rows[0].keys()))

    if fmt == "ar_aging":
        return (fmt, [map_ar_aging_row(r) for r in rows])
    elif fmt == "charge_capture":
        return (fmt, [map_charge_capture_row(r) for r in rows])
    elif fmt == "payment_posting":
        return (fmt, [map_payment_row(r) for r in rows])
    elif fmt == "claim_status":
        return (fmt, [map_claim_status_row(r) for r in rows])
    else:
        return (fmt or "unknown", rows)


# ── A/R Aging aggregation ──────────────────────────────────────────────────────

def aggregate_ar_aging(rows: List[Dict]) -> Dict:
    """
    Summarize AR aging data from normalized PrimeSuite rows.
    Returns aging buckets, payer breakdown, and total.
    """
    buckets = {
        "0_30": 0.0,
        "31_60": 0.0,
        "61_90": 0.0,
        "91_120": 0.0,
        "120_plus": 0.0,
        "total": 0.0,
    }
    payer_totals: Dict[str, float] = {}

    for row in rows:
        b0 = row.get("bucket_0_30") or 0.0
        b1 = row.get("bucket_31_60") or 0.0
        b2 = row.get("bucket_61_90") or 0.0
        b3 = row.get("bucket_91_120") or 0.0
        b4 = row.get("bucket_120_plus") or 0.0

        buckets["0_30"] += b0
        buckets["31_60"] += b1
        buckets["61_90"] += b2
        buckets["91_120"] += b3
        buckets["120_plus"] += b4
        buckets["total"] += b0 + b1 + b2 + b3 + b4

        payer = row.get("insurance_1") or "Self Pay / Unknown"
        if payer:
            payer_totals[payer] = payer_totals.get(payer, 0.0) + (b0 + b1 + b2 + b3 + b4)

    return {
        "buckets": buckets,
        "payer_totals": dict(sorted(payer_totals.items(), key=lambda x: -x[1])),
        "row_count": len(rows),
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _norm(col: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", col.lower().strip())


def _find(row: Dict, candidates: List[str]) -> Optional[str]:
    """Find first non-null value from a list of candidate column names."""
    row_lower = {_norm(k): v for k, v in row.items()}
    for c in candidates:
        nc = _norm(c)
        if nc in row_lower and row_lower[nc] not in (None, "", "nan", "NaN"):
            v = row_lower[nc]
            return str(v).strip() if v is not None else None
    return None


def _money(row: Dict, candidates: List[str]) -> float:
    """Extract a dollar amount from a row, cleaning $ and commas."""
    val = _find(row, candidates)
    if val is None:
        return 0.0
    try:
        cleaned = re.sub(r"[,$\s]", "", str(val).replace("(", "-").replace(")", ""))
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0


def _float(row: Dict, candidates: List[str]) -> float:
    val = _find(row, candidates)
    if val is None:
        return 1.0
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return 1.0
