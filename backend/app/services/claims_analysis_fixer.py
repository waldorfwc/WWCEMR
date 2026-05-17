"""
Fix PrimeSuite Claims Analysis exports before parsing.

Two issues handled:

1. **Phantom empty columns** — some exports include extra columns whose
   header row is blank and whose data is all-NaN (seen positions vary by
   export; e.g. Claim Analysis 2026.01.xls has 49 columns, 8 of them
   phantom). We drop those before further processing.

2. **Column shifts from missing cells** — when an optional cell is blank
   in the source (Electronic Filing Method ID, Filing Method, Filing Plan
   Name, Follow-Up Date/Reason, Last Submission Date, Plan Number,
   Policy Group Number), the export drops the cell and packs remaining
   values left, leaving trailing NaN. We realign per-row using per-column
   type validators.

Canonical 41-column Claims Analysis schema:

  0: Patient ID
  1: Patient Name
  2: Care Provider
  3: Insurance Class
  4: Claim Amount
  5: Aging 0-30 by Create Date
  6: Aging 31-60 by Create Date
  7: Aging 61-90 by Create Date
  8: Aging 91-120 by Create Date
  9: Aging 121+ by Create Date
 10: Aging 0-30 by Original Submission Date
 11: Aging 121+ by Original Submission Date
 12: Aging 31-60 by Original Submission Date
 13: Aging 61-90 by  Original Submission Date
 14: Aging 91-120 by Original Submission Date
 15: Claim Expected
 16: Claim ID
 17: Claim State
 18: Claim Status
 19: Date of Service
 20: Electronic Filing Method ID
 21: Filing Method
 22: Filing Plan Name
 23: Follow-Up Date
 24: Follow-Up Reason
 25: Insurance Adjustment
 26: Insurance Applied Amount
 27: Insurance Balance
 28: Insurance Company
 29: Insurance Paid Amount
 30: Insurance Priority
 31: Last Submission Date
 32: Line Balance
 33: Patient Balance
 34: Payor ID
 35: Plan Name
 36: Plan Number
 37: Policy Group Number
 38: Policy Number
 39: Practice Location
 40: Total Charges
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List

import pandas as pd

NCOLS = 41

CANONICAL_HEADERS = [
    "Patient ID", "Patient Name", "Care Provider", "Insurance Class",
    "Claim Amount",
    "Aging 0-30 by Create Date", "Aging 31-60 by Create Date",
    "Aging 61-90 by Create Date", "Aging 91-120 by Create Date",
    "Aging 121+ by Create Date",
    "Aging 0-30 by Original Submission Date",
    "Aging 121+ by Original Submission Date",
    "Aging 31-60 by Original Submission Date",
    "Aging 61-90 by  Original Submission Date",
    "Aging 91-120 by Original Submission Date",
    "Claim Expected", "Claim ID", "Claim State", "Claim Status",
    "Date of Service", "Electronic Filing Method ID", "Filing Method",
    "Filing Plan Name", "Follow-Up Date", "Follow-Up Reason",
    "Insurance Adjustment", "Insurance Applied Amount", "Insurance Balance",
    "Insurance Company", "Insurance Paid Amount", "Insurance Priority",
    "Last Submission Date", "Line Balance", "Patient Balance", "Payor ID",
    "Plan Name", "Plan Number", "Policy Group Number", "Policy Number",
    "Practice Location", "Total Charges",
]
assert len(CANONICAL_HEADERS) == NCOLS


@dataclass
class FixReport:
    total_rows: int
    unresolved_rows: int
    unresolved_samples: List[dict]
    phantom_columns_dropped: int
    shifts_detected: bool


# ------------ type tests ------------

def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _isblank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return _s(v) == ""


def is_number(v: Any) -> bool:
    if isinstance(v, (int, float)) and not pd.isna(v):
        return True
    try:
        float(_s(v))
        return True
    except Exception:
        return False


def is_int_in(v: Any, lo: int, hi: int) -> bool:
    try:
        f = float(v)
        if f != int(f):
            return False
        n = int(f)
        return lo <= n <= hi
    except Exception:
        return False


def is_date(v: Any) -> bool:
    if isinstance(v, (pd.Timestamp,)) and not pd.isna(v):
        return True
    return bool(re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", _s(v)))


# Finite enums
CLAIM_STATES = {"Open", "Closed"}
CLAIM_STATUSES = {
    "New/No EOB", "Paid In Full", "Paid Partial", "Balance Due",
    "Denied", "Rejected", "Void", "Pending", "Submitted",
    "Appealed", "Resubmitted",
}
PRIORITIES = {"Primary", "Secondary", "Tertiary", "Quaternary", "Patient", "Independent"}
FILING_METHODS = {"FTP", "CSV", "Paper", "Electronic", "Manual", "Fax"}
INS_CLASSES = {
    "BCBS", "MCO", "Medicare", "Medicaid", "Tricare", "Aetna", "Cigna",
    "Johns Hopkins", "United Healthcare", "Commercial", "No Primary Class",
}


def is_claim_state(v: Any) -> bool:
    return _s(v) in CLAIM_STATES


def is_claim_status(v: Any) -> bool:
    s = _s(v)
    if s in CLAIM_STATUSES:
        return True
    # Permissive fallback — any phrase not matching other column types
    if not s or is_date(v) or is_number(v):
        return False
    # Short human-readable phrase
    return 1 <= len(s) <= 40


def is_priority(v: Any) -> bool:
    return _s(v).capitalize() in PRIORITIES


def is_filing_method(v: Any) -> bool:
    s = _s(v)
    if not s or is_date(v):
        return False
    if s in FILING_METHODS:
        return True
    # Short alphanumeric identifier
    return len(s) <= 12 and bool(re.match(r"^[A-Za-z0-9 /-]+$", s))


def is_filing_plan_name(v: Any) -> bool:
    s = _s(v)
    if not s or is_date(v):
        return False
    return 3 <= len(s) <= 80


def is_follow_up_reason(v: Any) -> bool:
    s = _s(v)
    if not s or is_date(v):
        return False
    # Reject NPI-sized numbers or pure numbers
    if is_number(v) and not isinstance(v, str):
        return False
    return 1 <= len(s) <= 200


def is_ins_class(v: Any) -> bool:
    s = _s(v)
    if not s:
        return False
    if s in INS_CLASSES:
        return True
    # Free-form: non-numeric short-ish string
    if is_number(v) and not isinstance(v, str):
        return False
    return 1 <= len(s) <= 60 and not is_date(v)


def is_ins_company(v: Any) -> bool:
    s = _s(v)
    if not s or is_date(v):
        return False
    if is_number(v) and not isinstance(v, str):
        return False
    return 1 <= len(s) <= 120


def is_practice_location(v: Any) -> bool:
    s = _s(v)
    if not s:
        return False
    return (
        "WWC" in s or "Outpatient" in s or "Inpatient" in s
        or s.startswith("No Service")
        or "Gynecology" in s or "Aesthet" in s
    )


def is_patient_name(v: Any) -> bool:
    s = _s(v)
    return "," in s and 3 <= len(s) <= 80


def is_provider_name(v: Any) -> bool:
    return is_patient_name(v)


def is_str_nonempty(v: Any) -> bool:
    s = _s(v)
    return bool(s) and not is_date(v)


# ------------ per-column validators ------------

def col_ok(col: int, v: Any, vals: List[Any], val_idx: int) -> bool:
    """Does value `v` belong at column `col`? Uses lookahead for ambiguity."""
    if _isblank(v):
        return False

    if col == 0:       # Patient ID
        return is_int_in(v, 1, 99_999_999)
    if col == 1:       # Patient Name
        return is_patient_name(v)
    if col == 2:       # Care Provider
        return is_provider_name(v)
    if col == 3:       # Insurance Class
        return is_ins_class(v)
    if col == 4:       # Claim Amount
        return is_number(v)
    if 5 <= col <= 14:  # Aging buckets
        return is_number(v)
    if col == 15:      # Claim Expected
        return is_number(v)
    if col == 16:      # Claim ID — 5-7 digit int
        return is_int_in(v, 1_000, 9_999_999)
    if col == 17:      # Claim State
        return is_claim_state(v)
    if col == 18:      # Claim Status
        return is_claim_status(v)
    if col == 19:      # Date of Service
        return is_date(v)
    if col == 20:      # Electronic Filing Method ID — small int
        if is_date(v):
            return False
        return is_int_in(v, 1, 99_999)
    if col == 21:      # Filing Method
        return is_filing_method(v)
    if col == 22:      # Filing Plan Name
        # If v looks like a date, then cols 20-22 all blank and v = Follow-Up Date
        if is_date(v):
            return False
        return is_filing_plan_name(v)
    if col == 23:      # Follow-Up Date
        return is_date(v)
    if col == 24:      # Follow-Up Reason
        # Reject numbers (those belong to Insurance Adjustment col 25)
        if is_number(v) and not isinstance(v, str):
            return False
        return is_follow_up_reason(v)
    if 25 <= col <= 27:  # Insurance Adjustment / Applied / Balance
        return is_number(v)
    if col == 28:      # Insurance Company
        return is_ins_company(v)
    if col == 29:      # Insurance Paid Amount
        return is_number(v)
    if col == 30:      # Insurance Priority
        return is_priority(v)
    if col == 31:      # Last Submission Date
        return is_date(v)
    if col in (32, 33):  # Line Balance / Patient Balance
        return is_number(v)
    # Plan/Policy number fields — allow alphanumeric plus common punctuation
    # seen in real data (e.g. '476481-010-00001', 'W2869 27294', '33E',
    # 'PART A&B').
    _ID_CHARS = r"^[A-Z0-9 .\-/&]+$"

    if col == 34:      # Payor ID — short ID
        s = _s(v)
        if not s or len(s) > 20 or is_date(v):
            return False
        return bool(re.match(_ID_CHARS, s, re.IGNORECASE))
    if col == 35:      # Plan Name
        return is_str_nonempty(v) and len(_s(v)) <= 80
    if col == 36:      # Plan Number
        s = _s(v)
        if not s or len(s) > 25 or is_date(v):
            return False
        return bool(re.match(_ID_CHARS, s, re.IGNORECASE))
    if col == 37:      # Policy Group Number
        s = _s(v)
        if not s or len(s) > 30 or is_date(v):
            return False
        return bool(re.match(_ID_CHARS, s, re.IGNORECASE))
    if col == 38:      # Policy Number
        s = _s(v)
        if not s or len(s) > 40 or is_date(v):
            return False
        return bool(re.match(_ID_CHARS, s, re.IGNORECASE))
    if col == 39:      # Practice Location
        return is_practice_location(v)
    if col == 40:      # Total Charges
        return is_number(v)

    return True


# ------------ row fix ------------

def fix_row(row: List[Any], ncols: int = NCOLS):
    """Realign one row. Returns (fixed_list_of_ncols, leftover_list)."""
    row = list(row[:ncols])

    # Cols 0-4 are always filled (Patient ID, Name, Provider, Ins Class, Claim Amount)
    fixed: List[Any] = [None] * ncols
    for i in range(5):
        fixed[i] = row[i]

    vals = [v for v in row[5:] if not _isblank(v)]
    val_idx = 0
    col = 5
    while col < ncols:
        if val_idx >= len(vals):
            break
        v = vals[val_idx]
        if col_ok(col, v, vals, val_idx):
            fixed[col] = v
            val_idx += 1
        col += 1

    leftover = vals[val_idx:]
    return fixed, leftover


def drop_phantom_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop columns whose header row is NaN. Returns (df, n_dropped)."""
    if df.empty:
        return df, 0
    header = df.iloc[0]
    phantom = [i for i in range(df.shape[1]) if _isblank(header.iloc[i])]
    if not phantom:
        return df, 0
    keep = [i for i in range(df.shape[1]) if i not in phantom]
    return df.iloc[:, keep].reset_index(drop=True), len(phantom)


def _detect_systematic_leading_offset(df: pd.DataFrame) -> int:
    """Some Claims Analysis exports put all data rows one column to the
    right of their headers (header at col 0, but every data row has NaN at
    col 0 and the actual Patient ID at col 1). Returns the number of
    leading columns to strip from data rows (0, 1, or 2)."""
    if df.empty or len(df) < 2:
        return 0
    data = df.iloc[1:, :]
    # Look at up to the first 50 rows to sample
    sample = data.head(50) if len(data) > 50 else data
    n = len(sample)
    if n == 0:
        return 0
    for offset in (1, 2):
        # Are all N leftmost columns NaN in every sampled data row?
        if offset >= df.shape[1]:
            break
        leading_nan = sample.iloc[:, :offset].isna().all(axis=None)
        # And is there a meaningful value at column `offset` in most rows?
        filled_next = sample.iloc[:, offset].notna().sum()
        if leading_nan and filled_next >= int(n * 0.9):
            return offset
    return 0


def _has_canonical_29col_headers(df: pd.DataFrame) -> bool:
    """True when the DataFrame's named columns exactly match the 29-col
    Claims Analysis lean schema. In that case pandas has already mapped each
    cell to the right column by name and per-row realignment would actively
    corrupt the data (the column-position validators in `col_ok` are written
    for the legacy 41-col layout)."""
    expected = {
        "Patient ID", "Patient Name", "Date of Service", "Care Provider",
        "Insurance Class", "Claim Amount", "Claim Expected", "Claim ID",
        "Claim State", "Claim Status", "Electronic Filing Method ID",
        "Filing Method", "Filing Plan Name", "Follow-Up Date",
        "Follow-Up Reason", "Insurance Adjustment", "Insurance Applied Amount",
        "Insurance Balance", "Insurance Company", "Insurance Paid Amount",
        "Insurance Priority", "Last Submission Date", "Line Balance",
        "Patient Balance", "Payor ID", "Plan Name", "Policy Number",
        "Practice Location", "Total Charges",
    }
    if df.empty:
        return False
    # fix_file reads with header=None, so the actual column names live in row 0.
    # Fall back to df.columns when headers are already promoted.
    header_row = df.iloc[0].tolist()
    actual = {str(v).strip() for v in header_row if not _isblank(v)}
    if not actual or not expected.issubset(actual):
        actual = {str(c).strip() for c in df.columns if not str(c).startswith("Unnamed")}
    return expected.issubset(actual)


def fix_dataframe(df: pd.DataFrame):
    """Return (fixed_df, report_list, phantom_dropped_count, leading_offset)."""
    df, phantom_dropped = drop_phantom_columns(df)

    # Lean 29-col schema: pandas already maps cells to the right named column.
    # Skip the row-realignment pass — it was written for a 41-col layout and
    # silently nulls Claim State / Claim Status when applied here.
    if _has_canonical_29col_headers(df):
        return df.reset_index(drop=True), [], phantom_dropped, 0

    if df.shape[1] < NCOLS:
        raise ValueError(
            f"Claims Analysis fixer expected at least {NCOLS} columns after "
            f"dropping phantom columns, got {df.shape[1]}"
        )

    leading_offset = _detect_systematic_leading_offset(df)
    if leading_offset:
        # Shift data rows left by `leading_offset`; keep the header as-is.
        header = df.iloc[[0]].reset_index(drop=True)
        data = df.iloc[1:, leading_offset:].reset_index(drop=True)
        # Re-index data columns to start at 0 so the combined DataFrame lines up
        data.columns = range(data.shape[1])
        # Pad data with empty trailing columns so it has the same width as header
        while data.shape[1] < header.shape[1]:
            data[data.shape[1]] = pd.NA
        df = pd.concat([header, data.iloc[:, : header.shape[1]]], ignore_index=True)

    out: List[List[Any]] = []
    report: List[dict] = []
    for i, row in df.iterrows():
        if i == 0:
            out.append(list(row[:NCOLS]))
            continue
        fixed, leftover = fix_row(row.tolist())
        out.append(fixed)
        if leftover:
            report.append({
                "row_idx": int(i),
                "leftover_count": len(leftover),
                "leftover": leftover,
            })
    return pd.DataFrame(out), report, phantom_dropped, leading_offset


EXPECTED_HEADER_FIRST = "Patient ID"


def is_claims_analysis_file(path: str) -> bool:
    try:
        probe = pd.read_excel(path, sheet_name=0, header=None, nrows=1)
        return str(probe.iloc[0, 0]).strip() == EXPECTED_HEADER_FIRST
    except Exception:
        return False


def fix_file(src_path: str, dst_path: str) -> FixReport:
    """Read a Claims Analysis export, drop phantom cols + realign shifts,
    write the result as .xlsx at dst_path."""
    df = pd.read_excel(src_path, sheet_name=0, header=None)
    shifts_detected = _any_row_has_trailing_nan(df)

    fixed_df, report, phantom_dropped, leading_offset = fix_dataframe(df)

    headers = fixed_df.iloc[0, :NCOLS].tolist()
    # If phantom columns were dropped but the headers on the remaining
    # columns are still good (as expected), use them. Otherwise fall back
    # to the canonical names.
    if any(_isblank(h) for h in headers):
        headers = list(CANONICAL_HEADERS)
    out = fixed_df.iloc[1:, :NCOLS].copy()
    out.columns = headers
    out.to_excel(dst_path, index=False)

    return FixReport(
        total_rows=len(out),
        unresolved_rows=len(report),
        unresolved_samples=[
            {"row_idx": r["row_idx"], "leftover": r["leftover"]}
            for r in report[:10]
        ],
        phantom_columns_dropped=phantom_dropped,
        shifts_detected=(
            shifts_detected or phantom_dropped > 0 or leading_offset > 0
        ),
    )


def _any_row_has_trailing_nan(df: pd.DataFrame) -> bool:
    """True if any row has a blank in its last column — a shift indicator.
    Checks the raw frame (before dropping phantom cols), so phantom NaN
    tails at the right are normal; we're looking for genuinely truncated
    rows where a real column is blank at the end."""
    # Find the right-most column whose header is non-blank
    if df.empty:
        return False
    header = df.iloc[0]
    last_real = None
    for i in range(df.shape[1] - 1, -1, -1):
        if not _isblank(header.iloc[i]):
            last_real = i
            break
    if last_real is None:
        return False
    data = df.iloc[1:, last_real]
    return bool(data.isna().any())
