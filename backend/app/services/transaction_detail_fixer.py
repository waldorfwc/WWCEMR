"""
Fix column-shifted Transaction Detail exports from PrimeSuite.

Same root cause as Charge / Claims Analysis: when an optional cell is
blank in the source, PrimeSuite drops the cell and packs the remaining
values left, leaving trailing NaN. We realign per-row using per-column
type validators.

The 60-column canonical schema (in order):

   0  Patient: Patient ID
   1  Patient: Name
   2  Patient: First Name
   3  Patient: Last Name
   4  Patient: Date Of Birth
   5  Transaction: Visit ID
   6  Transaction: Charge Ticket Number
   7  Transaction: Type
   8  Patient: Address Line 1
   9  Patient: Address Line 2
  10  Patient: City
  11  Patient: State
  12  Patient: Zip Code
  13  Patient: Phone Primary
  14  Patient: EMail
  15  Patient: Sex
  16  Date: Date of Service
  17  Date: Posting Date
  18  Date: Create Date
  19  Date: Original Posting Date
  20  Date: Original Create Date
  21  Transaction: Amount - Charge Voids
  22  Transaction: Amount - Adjustment Voids
  23  Transaction: Amount - Adjustment Offsets
  24  Transaction: Amount - Payment Voids
  25  Transaction: Amount - Payment Offsets
  26  Transaction: Amount - Transaction Amount
  27  Transaction: Adjustment Type
  28  Transaction: Adjustment Sub-Type
  29  Transaction: Applied To
  30  Transaction: Payment Method
  31  Transaction: Payment Supplier
  32  Transaction: Payment/Adjustment Source
  33  Transaction: Payment/Adjustment Additional Info
  34  Transaction: Amount - Net Charges
  35  Transaction: Amount - Net Adjustments
  36  Transaction: Amount - Net Payments
  37  Transaction: Amount - Gross Charges
  38  Transaction: Amount - Gross Adjustments
  39  Transaction: Amount - Gross Insurance Payments
  40  Transaction: Amount - Gross Patient/Other Payments
  41  Transaction: Amount - Gross Payments
  42  Transaction: Procedure Code
  43  Transaction: Procedure Description
  44  Transaction: Procedure Modifiers
  45  Transaction: Diagnosis ICD10 Codes
  46  Transaction: Net Charge Units
  47  Transaction: Description
  48  Orginal Transaction: Transaction Amount
  49  Original Transaction: Payment/Adjustment Source
  50  Transaction: Billable Provider Name
  51  Transaction: Rendering Provider Name
  52  Transaction: Referring Provider Name
  53  Transaction: Practice Location
  54  Transaction: Service Location
  55  Transaction: User
  56  Transaction: Void Indicator
  57  Transaction: ERA Indicator
  58  Transaction: Charge Override Indicator
  59  Transaction: Refund Check Number
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List

import pandas as pd

NCOLS = 60

CANONICAL_HEADERS = [
    "Patient: Patient ID", "Patient: Name", "Patient: First Name",
    "Patient: Last Name", "Patient: Date Of Birth",
    "Transaction: Visit ID", "Transaction: Charge Ticket Number",
    "Transaction: Type",
    "Patient: Address Line 1", "Patient: Address Line 2",
    "Patient: City", "Patient: State", "Patient: Zip Code",
    "Patient: Phone Primary", "Patient: EMail", "Patient: Sex",
    "Date: Date of Service", "Date: Posting Date", "Date: Create Date",
    "Date: Original Posting Date", "Date: Original Create Date",
    "Transaction: Amount - Charge Voids",
    "Transaction: Amount - Adjustment Voids",
    "Transaction: Amount - Adjustment Offsets",
    "Transaction: Amount - Payment Voids",
    "Transaction: Amount - Payment Offsets",
    "Transaction: Amount - Transaction Amount",
    "Transaction: Adjustment Type", "Transaction: Adjustment Sub-Type",
    "Transaction: Applied To", "Transaction: Payment Method",
    "Transaction: Payment Supplier",
    "Transaction: Payment/Adjustment Source",
    "Transaction: Payment/Adjustment Additional Info",
    "Transaction: Amount - Net Charges",
    "Transaction: Amount - Net Adjustments",
    "Transaction: Amount - Net Payments",
    "Transaction: Amount - Gross Charges",
    "Transaction: Amount - Gross Adjustments",
    "Transaction: Amount - Gross Insurance Payments",
    "Transaction: Amount - Gross Patient/Other Payments",
    "Transaction: Amount - Gross Payments",
    "Transaction: Procedure Code", "Transaction: Procedure Description",
    "Transaction: Procedure Modifiers",
    "Transaction: Diagnosis ICD10 Codes",
    "Transaction: Net Charge Units", "Transaction: Description",
    "Orginal Transaction: Transaction Amount",
    "Original Transaction: Payment/Adjustment Source",
    "Transaction: Billable Provider Name",
    "Transaction: Rendering Provider Name",
    "Transaction: Referring Provider Name",
    "Transaction: Practice Location", "Transaction: Service Location",
    "Transaction: User", "Transaction: Void Indicator",
    "Transaction: ERA Indicator",
    "Transaction: Charge Override Indicator",
    "Transaction: Refund Check Number",
]
assert len(CANONICAL_HEADERS) == NCOLS

VALID_TYPES = {
    "CHG", "PMT", "C-ADJ", "D-ADJ", "V-CHG", "V-PMT", "V-ADJ", "V-C-ADJ",
    "V-D-ADJ", "SLT-TO", "SLT-FROM", "MTC-TO", "MTC-FROM", "OFFSET",
    "RFND", "REFUND", "WO", "F.Chg",
}
US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC","PR","VI","GU","AS","MP",
}
ADJ_TYPES = {"Contractual", "In-House", "Administrative", "Bad Debt",
             "Charity", "Refund", "FINANCE CHARGES", "FINANCE CHARGES REVERSAL"}
PMT_SOURCES = {"Insurance", "Patient", "Other", "Patient/Other"}
PMT_METHODS = {"Credit Card", "Check", "Cash", "EFT", "ERA", "Debit Card",
               "Money Order", "Wire", "ACH"}
ERA_FLAGS = {"YES", "NO", "EFT"}
YES_NO = {"YES", "NO"}
SEX_VALS = {"Male", "Female", "Unknown"}


@dataclass
class FixReport:
    total_rows: int
    rows_realigned: int
    unresolved_rows: int
    unresolved_samples: List[dict]
    shifts_detected: bool


# ---------------------------- type tests ---------------------------- #

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


def is_int_in(v, lo, hi):
    try:
        f = float(v)
        if f != int(f):
            return False
        return lo <= int(f) <= hi
    except Exception:
        return False


def is_number(v):
    if isinstance(v, (int, float)) and not pd.isna(v):
        return True
    try:
        float(_s(v))
        return True
    except Exception:
        return False


def is_date(v):
    s = _s(v)
    return bool(re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", s))


def is_phone(v):
    s = _s(v)
    if re.match(r"^\d{3}-\d{3}-\d{4}$", s):
        return True
    if re.match(r"^[2-9]\d{9}$", s):
        return True
    return False


def is_email(v):
    s = _s(v)
    return "@" in s and "." in s and len(s) <= 100


def is_state(v):
    return _s(v).upper() in US_STATES


def is_zip(v):
    s = _s(v).split(".")[0]
    return bool(re.match(r"^\d{5}(-\d{4})?$", s))


def is_sex(v):
    return _s(v).capitalize() in SEX_VALS


def is_yes_no(v):
    return _s(v).upper() in YES_NO


def is_era_flag(v):
    return _s(v).upper() in ERA_FLAGS


def is_type(v):
    return _s(v) in VALID_TYPES


def is_adj_type(v):
    s = _s(v)
    return s in ADJ_TYPES or s == "0" or s == "" or s == "No CARC CODE"


def is_pmt_source(v):
    s = _s(v)
    return s in PMT_SOURCES


def is_pmt_method(v):
    s = _s(v)
    if not s:
        return False
    return s in PMT_METHODS or len(s) <= 30


def is_provider_name(v):
    s = _s(v)
    if not s:
        return False
    if s.startswith("No "):    # "No Referring Provider", "No Billable Provider"
        return True
    return "," in s and 3 <= len(s) <= 80


def is_location(v):
    s = _s(v)
    if not s:
        return False
    return ("WWC" in s) or s.startswith("No Service") or s.startswith("Outpatient") \
        or s.startswith("Inpatient") or "Hospital" in s or "Center" in s \
        or "Gynecology" in s


def is_proc_code(v):
    s = _s(v)
    if not s:
        return False
    # CPT/HCPCS: 5-char alphanumeric; or short codes like 'F.Chg', 'PELLE', 'NOSHW'
    return bool(re.match(r"^[A-Z0-9.\-]{1,15}$", s, re.IGNORECASE))


def is_modifier(v):
    s = _s(v)
    if not s:
        return False
    if s == "0":
        return True
    return bool(re.match(r"^[A-Z0-9]{1,3}(,[A-Z0-9]{1,3})*$", s, re.IGNORECASE))


def is_diag_codes(v):
    s = _s(v)
    if not s:
        return False
    # ICD-10 codes possibly multiple separated by spaces
    return bool(re.match(r"^([A-Z]\d{1,2}(\.[A-Z0-9]+)?\s*)+$", s, re.IGNORECASE)) or s == "0"


def is_str_nonempty(v):
    return bool(_s(v))


def is_applied_to(v):
    """Visit ID number, 'Procedure Code', or blank."""
    if _isblank(v):
        return True
    s = _s(v)
    if s == "Procedure Code":
        return True
    return is_int_in(v, 1, 99_999_999)


def is_charge_ticket_number(v):
    """Usually 6-7 digit int, but for finance charges might be a code like 'FC'."""
    if _isblank(v):
        return True
    if is_int_in(v, 100, 99_999_999):
        return True
    s = _s(v)
    return bool(re.match(r"^[A-Z]{1,4}\d*$", s))  # e.g. 'FC', 'FC1'


def is_visit_id(v):
    return _isblank(v) or is_int_in(v, 100, 99_999_999)


# ---------------------------- col_ok ---------------------------- #

def col_ok(col: int, v: Any, vals: list, val_idx: int) -> bool:
    if _isblank(v):
        return False

    if col == 0:  return is_int_in(v, 1, 99_999_999)              # Patient ID
    if col == 1:  return is_provider_name(v) or "," in _s(v)      # Patient Name
    if col == 2:  return bool(_s(v)) and not is_date(v)           # First Name
    if col == 3:  return bool(_s(v)) and not is_date(v)           # Last Name
    if col == 4:  return is_date(v)                               # DOB
    if col == 5:  return is_int_in(v, 100, 99_999_999)            # Visit ID (REQUIRED if at this position)
    if col == 6:  return is_charge_ticket_number(v)               # Charge Ticket Number
    if col == 7:  return is_type(v)                               # Type — strict enum

    # Demographics — most strings work
    if col == 8:  return is_str_nonempty(v) and not is_state(v)   # Address Line 1
    if col == 9:                                                  # Address Line 2 (often blank)
        # If next is a state code, current is City (Addr2 missing)
        if val_idx + 1 < len(vals) and is_state(vals[val_idx + 1]):
            return False
        if is_state(v):
            return False
        return is_str_nonempty(v) and not is_zip(v)
    if col == 10: return is_str_nonempty(v) and not is_state(v)  # City
    if col == 11: return is_state(v)                             # State
    if col == 12: return is_zip(v)                               # Zip
    if col == 13:                                                # Phone (often blank)
        if is_email(v):
            return False
        return is_phone(v)
    if col == 14:                                                # EMail (often blank)
        if is_date(v):
            return False
        return is_email(v)
    if col == 15: return is_sex(v)                               # Sex

    # Dates 16-20
    if 16 <= col <= 20: return is_date(v)

    # Money 21-26 (voids/offsets/transaction amount)
    if 21 <= col <= 26: return is_number(v) and not is_date(v)

    if col == 27: return is_adj_type(v)                          # Adjustment Type
    if col == 28: return is_str_nonempty(v) and not is_number(v) # Sub-Type or "0"
    if col == 29: return is_applied_to(v)                        # Applied To
    if col == 30: return is_pmt_method(v) and not is_number(v)   # Payment Method
    if col == 31: return is_str_nonempty(v) and not is_date(v)   # Payment Supplier
    if col == 32:                                                # Payment/Adjustment Source
        return is_pmt_source(v) or _s(v) in {"0", ""}
    if col == 33: return is_str_nonempty(v)                      # Additional Info

    # Money 34-41
    if 34 <= col <= 41: return is_number(v) and not is_date(v)

    if col == 42: return is_proc_code(v)                         # Procedure Code
    if col == 43: return is_str_nonempty(v) and not is_number(v) # Description
    if col == 44:                                                # Modifiers (often blank or "0")
        return is_modifier(v) or _s(v) == "0"
    if col == 45:                                                # ICD10 Diag Codes
        return is_str_nonempty(v) and not is_number(v)
    if col == 46: return is_int_in(v, 0, 999)                    # Net Charge Units
    if col == 47: return is_str_nonempty(v)                      # Description (long)
    if col == 48: return is_number(v) and not is_date(v)         # Orginal Tx Amount
    if col == 49: return is_pmt_source(v) or _s(v) in {"0", ""}  # Original Pmt Source

    if col == 50: return is_provider_name(v)                     # Billable Provider Name
    if col == 51: return is_provider_name(v)                     # Rendering Provider Name
    if col == 52: return is_provider_name(v)                     # Referring Provider Name
    if col == 53: return is_location(v)                          # Practice Location
    if col == 54: return is_location(v)                          # Service Location
    if col == 55: return is_str_nonempty(v) and not is_date(v)   # User
    if col == 56: return is_yes_no(v)                            # Void Indicator
    if col == 57: return is_era_flag(v)                          # ERA Indicator
    if col == 58: return is_yes_no(v)                            # Charge Override Indicator
    if col == 59: return is_str_nonempty(v)                      # Refund Check Number

    return True


# ---------------------------- row fix ---------------------------- #

def fix_row(row: list) -> tuple[list, list]:
    row = list(row[:NCOLS])

    # Cols 0-4 always present (Patient ID, Name, First, Last, DOB).
    fixed: list = [None] * NCOLS
    for i in range(5):
        fixed[i] = row[i]

    # Walk from col 5 onward, greedily aligning.
    vals = [v for v in row[5:] if not _isblank(v)]
    val_idx = 0
    col = 5
    while col < NCOLS:
        if val_idx >= len(vals):
            break
        v = vals[val_idx]
        if col_ok(col, v, vals, val_idx):
            fixed[col] = v
            val_idx += 1
        col += 1

    leftover = vals[val_idx:]
    return fixed, leftover


def _row_needs_fix(row: list) -> bool:
    """A row is ALREADY aligned only when MULTIPLE landmark columns
    hold values matching their expected types:

      - col 7  (Type)         in VALID_TYPES
      - col 11 (State)        is a US state OR blank
      - col 16 (DOS)          is a date OR blank
      - col 34 (Net Charges)  is a number OR blank
      - col 42 (CPT)          is a procedure code OR blank
      - col 50 (Billable)     is a provider name OR blank

    If ANY landmark fails, the row is shifted and needs realignment.
    """
    if len(row) <= 50:
        return True

    # Type — strongest landmark
    if not is_type(row[7]):
        return True

    # State — must be a US state if present
    if not _isblank(row[11]):
        if not is_state(row[11]):
            return True

    # DOS — must be a date if present
    if not _isblank(row[16]) and not is_date(row[16]):
        return True

    # Net Charges — must be numeric if present
    if not _isblank(row[34]):
        try:
            float(row[34])
        except (TypeError, ValueError):
            return True

    # Procedure Code — must look like a CPT/HCPCS if present
    if not _isblank(row[42]):
        s = _s(row[42])
        if not re.match(r"^[A-Z0-9.\- ]{1,15}$", s, re.IGNORECASE):
            return True

    # Billable Provider Name — must look like a provider if present
    if not _isblank(row[50]):
        s = _s(row[50])
        if not (s.startswith("No ") or "," in s):
            return True

    return False


def fix_dataframe(df: pd.DataFrame):
    """Returns (fixed_df, report_list, rows_realigned_count).

    Already-aligned rows pass through untouched (any disturbance is more
    likely to break them than help). Only rows that fail landmark checks
    go through the validator-walker realignment.
    """
    out: list = []
    report: list = []
    rows_realigned = 0
    for i, row in df.iterrows():
        if i == 0:
            out.append(list(row[:NCOLS]))
            continue
        raw = row.tolist()
        if _row_needs_fix(raw):
            rows_realigned += 1
            fixed, leftover = fix_row(raw)
            out.append(fixed)
            if leftover:
                report.append({
                    "row_idx": int(i),
                    "leftover_count": len(leftover),
                    "leftover": leftover[:10],
                })
        else:
            # Pass through truncated/padded
            padded = raw[:NCOLS] + [None] * max(0, NCOLS - len(raw))
            out.append(padded)
    return pd.DataFrame(out), report, rows_realigned


def fix_file(src_path: str, dst_path: str) -> FixReport:
    df = pd.read_excel(src_path, sheet_name=0, header=None)
    fixed_df, report, rows_realigned = fix_dataframe(df)

    headers = fixed_df.iloc[0, :NCOLS].tolist()
    if any(_isblank(h) for h in headers):
        headers = list(CANONICAL_HEADERS)
    out = fixed_df.iloc[1:, :NCOLS].copy()
    out.columns = headers
    out.to_excel(dst_path, index=False)

    return FixReport(
        total_rows=len(out),
        rows_realigned=rows_realigned,
        unresolved_rows=len(report),
        unresolved_samples=[
            {"row_idx": r["row_idx"], "leftover": r["leftover"]}
            for r in report[:10]
        ],
        shifts_detected=rows_realigned > 0,
    )
