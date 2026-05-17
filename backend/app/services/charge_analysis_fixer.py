"""
Fix column-shifted Charge Analysis exports from PrimeSuite.

Problem: PrimeSuite's XLS export drops blank cells and packs the remaining
values left, leaving trailing blank columns. With ~10+ optional fields per
row that can be blank (Address Line 2, EMail, Last Note fields, Financially
Responsible, Visit Primary Care Provider, etc.), nearly every row in a
modern Charge Analysis pull is shifted.

Solution: For each row, walk left-to-right and use per-column type
validators to detect shifts, inserting blanks where the source cell was
omitted.

The 69-column canonical schema (Charge Analysis with credit fields +
demographics added):

   0  Patient: Patient ID
   1  Patient: First Name
   2  Patient: Last Name
   3  Date: Service date of the Charge
   4  Procedure: Code
   5  Provider: Rendering
   6  Location: Service Location
   7  Visit: Visit Type
   8  Adjustment: Net Non-Primary Ins. Adjusted
   9  Adjustment: Net Patient/Other Adjusted
  10  Adjustment: Net Primary Ins. Adjusted
  11  Charge Balance: Collection
  12  Charge Balance: Insurance
  13  Charge Balance: Patient
  14  Charge Balance: Total
  15  Charge: Charge Amount
  16  Charge: Charge Ticket ID
  17  Charge: Co-Pay
  18  Charge: Gross Charges
  19  Charge: Last Note Create Date
  20  Charge: Last Note description
  21  Charge: Last Note user
  22  Charge: Net Units
  23  Charge: User Name
  24  Charge: Void Indicator
  25  Date: Create date of the Charge
  26  Date: Posting date of the Charge
  27  Diagnosis: Primary Code
  28  Diagnosis: Primary Code Description
  29  Diagnosis: Primary ICD-10 Code
  30  Diagnosis: Primary ICD-10 Code Description
  31  Financially Responsible: Last Name
  32  Financially Responsible: Name
  33  Financially Responsible: Patient ID
  34  Insurance: Charge Primary Ins. Category
  35  Insurance: Charge Primary Ins. Class
  36  Insurance: Charge Primary Ins. Company
  37  Insurance: Charge Primary Ins. Plan
  38  Insurance: Charge Primary Policy Number
  39  Insurance: Charge Secondary Ins. Category
  40  Insurance: Charge Secondary Ins. Class
  41  Insurance: Charge Secondary Ins. Company
  42  Insurance: Charge Secondary Ins. Plan
  43  Insurance: Charge Secondary Policy Number
  44  Patient: Address Line 1
  45  Patient: Address Line 2
  46  Patient: City
  47  Patient: Credit Insurance
  48  Patient: Credit Patient
  49  Patient: Credit Pre-Pay
  50  Patient: Credit Undetermined
  51  Patient: Date Of Birth
  52  Patient: EMail
  53  Patient: Patient Name
  54  Patient: State
  55  Patient: Zip Code
  56  Payment: Net Applied Payment
  57  Payment: Net Non-Primary Ins. Applied
  58  Payment: Net Patient/Other Applied
  59  Payment: Net Primary Ins. Applied
  60  Procedure: Description
  61  Procedure: Modifiers
  62  Procedure: Procedure Charge
  63  Provider: Billable NPI
  64  Provider: Rendering NPI
  65  Visit: Primary Care Provider
  66  Visit: VisitID
  67  Patient: Phone Primary
  68  Patient: Sex
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List

import pandas as pd

NCOLS = 69


CANONICAL_HEADERS = [
    "Patient: Patient ID", "Patient: First Name", "Patient: Last Name",
    "Date: Service date of the Charge", "Procedure: Code",
    "Provider: Rendering", "Location: Service Location", "Visit: Visit Type",
    "Adjustment: Net Non-Primary Ins. Adjusted",
    "Adjustment: Net Patient/Other Adjusted",
    "Adjustment: Net Primary Ins. Adjusted",
    "Charge Balance: Collection", "Charge Balance: Insurance",
    "Charge Balance: Patient", "Charge Balance: Total",
    "Charge: Charge Amount", "Charge: Charge Ticket ID", "Charge: Co-Pay",
    "Charge: Gross Charges",
    "Charge: Last Note Create Date", "Charge: Last Note description",
    "Charge: Last Note user", "Charge: Net Units", "Charge: User Name",
    "Charge: Void Indicator",
    "Date: Create date of the Charge", "Date: Posting date of the Charge",
    "Diagnosis: Primary Code", "Diagnosis: Primary Code Description",
    "Diagnosis: Primary ICD-10 Code",
    "Diagnosis: Primary ICD-10 Code Description",
    "Financially Responsible: Last Name", "Financially Responsible: Name",
    "Financially Responsible: Patient ID",
    "Insurance: Charge Primary Ins. Category",
    "Insurance: Charge Primary Ins. Class",
    "Insurance: Charge Primary Ins. Company",
    "Insurance: Charge Primary Ins. Plan",
    "Insurance: Charge Primary Policy Number",
    "Insurance: Charge Secondary Ins. Category",
    "Insurance: Charge Secondary Ins. Class",
    "Insurance: Charge Secondary Ins. Company",
    "Insurance: Charge Secondary Ins. Plan",
    "Insurance: Charge Secondary Policy Number",
    "Patient: Address Line 1", "Patient: Address Line 2", "Patient: City",
    "Patient: Credit Insurance", "Patient: Credit Patient",
    "Patient: Credit Pre-Pay", "Patient: Credit Undetermined",
    "Patient: Date Of Birth", "Patient: EMail", "Patient: Patient Name",
    "Patient: State", "Patient: Zip Code",
    "Payment: Net Applied Payment",
    "Payment: Net Non-Primary Ins. Applied",
    "Payment: Net Patient/Other Applied",
    "Payment: Net Primary Ins. Applied",
    "Procedure: Description", "Procedure: Modifiers",
    "Procedure: Procedure Charge",
    "Provider: Billable NPI", "Provider: Rendering NPI",
    "Visit: Primary Care Provider", "Visit: VisitID",
    "Patient: Phone Primary", "Patient: Sex",
]
assert len(CANONICAL_HEADERS) == NCOLS


# ────────────── type tests ──────────────

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


def is_number(v):
    if isinstance(v, (int, float)) and not pd.isna(v):
        return True
    try:
        float(_s(v))
        return True
    except Exception:
        return False


def is_int_in(v, lo, hi):
    try:
        f = float(v)
        if f != int(f):
            return False
        n = int(f)
        return lo <= n <= hi
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
    if s.lower() in ("cell", "home", "work"):
        return True
    return False


def is_npi(v):
    return is_int_in(v, 1_000_000_000, 9_999_999_999)


US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC","PR","VI","GU","AS","MP",
}


def is_state(v):
    return _s(v).upper() in US_STATES


def is_zip(v):
    s = _s(v).split(".")[0]
    return bool(re.match(r"^\d{5}(-\d{4})?$", s))


def is_yes_no(v):
    return _s(v).upper() in ("YES", "NO")


def is_sex(v):
    return _s(v).capitalize() in ("Male", "Female", "Unknown", "Other")


def is_email(v):
    s = _s(v)
    return "@" in s and "." in s and len(s) <= 100 and not is_date(v)


def is_modifier(v):
    """Modifier: blank, '0', 1-3 char alphanumeric, or comma-list."""
    s = _s(v)
    if not s or s == "0":
        return True
    if re.match(r"^[A-Z0-9]{1,5}$", s, re.IGNORECASE):
        return True
    if re.match(r"^[A-Z0-9]{1,5}(,[A-Z0-9]{1,5})+$", s, re.IGNORECASE):
        return True
    return False


def is_proc_code(v):
    s = _s(v)
    if not s:
        return False
    return bool(re.match(r"^[A-Z0-9.\- ]{1,15}$", s, re.IGNORECASE))


def is_visit_type(v):
    s = _s(v)
    if not s:
        return False
    keywords = ["Visit", "Procedure", "Pelle", "Outpatient", "Inpatient",
                "Retail", "Sculpt", "Device", "Coolsculpting", "VOIDED"]
    if any(k in s for k in keywords) or s == "No Visit Type":
        return True
    return False


def is_location(v):
    s = _s(v)
    if not s:
        return False
    return ("WWC" in s) or ("Outpatient" in s) or ("Inpatient" in s) \
        or s.startswith("No Service") or "Hospital" in s


def is_provider_name(v):
    """Last, First TITLE format, OR 'No Rendering Provider' etc."""
    s = _s(v)
    if not s:
        return False
    if s.startswith("No "):
        return True
    return "," in s and 3 <= len(s) <= 80


def is_diag_code(v):
    """ICD-9-style: numeric with optional letter prefix, e.g. 627.1, V72.31"""
    if isinstance(v, (int, float)) and not pd.isna(v):
        s = f"{float(v):.6g}"
    else:
        s = _s(v)
    return bool(re.match(r"^[A-Z]?\d+(\.\d+)?$", s, re.IGNORECASE)) and 1 <= len(s) <= 10


def is_icd10(v):
    s = _s(v)
    return bool(re.match(r"^[A-Z]\d{1,2}(\.[A-Z0-9]+)?$", s, re.IGNORECASE)) \
        and 2 <= len(s) <= 10


def is_ticket_id(v):
    return is_int_in(v, 100_000, 9_999_999)


def is_visit_id(v):
    return is_int_in(v, 100_000, 9_999_999)


def is_policy_num(v):
    """Permissive — alphanumeric ID, free text like 'SELF PAY', etc."""
    s = _s(v)
    if not s or len(s) > 40:
        return False
    if is_date(v) or is_state(v):
        return False
    return True


def is_ins_string(v):
    """Insurance class/company/plan name — non-empty, not a date/number/state/etc."""
    if isinstance(v, (int, float)) and not isinstance(v, str) and not pd.isna(v):
        return False
    s = _s(v)
    if not s:
        return False
    if is_date(v) or is_phone(v) or is_state(v) or is_npi(v):
        return False
    if is_zip(v) or is_yes_no(v) or is_sex(v):
        return False
    return True


def is_str_nonempty(v):
    s = _s(v)
    return bool(s) and not is_date(v)


# ────────────── per-column validator ──────────────

def col_ok(col: int, v: Any, vals: list, val_idx: int) -> bool:
    if _isblank(v):
        return False

    # Identity (always present)
    if col == 0:  return is_int_in(v, 1, 99_999_999)              # Patient ID
    if col == 1:  return is_str_nonempty(v) and not is_int_in(v, 0, 99_999_999)  # First Name
    if col == 2:  return is_str_nonempty(v) and not is_int_in(v, 0, 99_999_999)  # Last Name
    if col == 3:  return is_date(v)                                # DOS
    if col == 4:  return is_proc_code(v)                           # CPT

    # Provider / Location / Visit Type
    if col == 5:  return is_provider_name(v)                       # Rendering Provider
    if col == 6:  return is_location(v)                            # Service Location
    if col == 7:  return is_visit_type(v)                          # Visit Type

    # Money 8-15
    if 8 <= col <= 15:
        return is_number(v) and not is_date(v)

    # Charge Ticket ID / Co-Pay / Gross
    if col == 16: return is_ticket_id(v)                           # Charge Ticket ID
    if col == 17: return is_number(v) and not is_date(v)           # Co-Pay
    if col == 18: return is_number(v) and not is_date(v)           # Gross Charges

    # Last Note (mostly blank)
    if col == 19: return is_date(v)                                # Last Note Create Date
    if col == 20:                                                  # Last Note description
        return is_str_nonempty(v) and not is_date(v) and not is_number(v) \
            and len(_s(v)) >= 3 and len(_s(v)) <= 200
    if col == 21:                                                  # Last Note user
        return is_str_nonempty(v) and not is_date(v) and not is_number(v) \
            and len(_s(v)) <= 50

    if col == 22: return is_int_in(v, 0, 999)                      # Net Units
    if col == 23:                                                  # Charge: User Name
        return is_str_nonempty(v) and not is_date(v) and not is_number(v) \
            and len(_s(v)) <= 50
    if col == 24: return is_yes_no(v)                              # Void Indicator

    if col == 25: return is_date(v)                                # Create Date
    if col == 26: return is_date(v)                                # Posting Date

    # Diagnosis (often blank for some procedures, present for E/M)
    if col == 27: return is_diag_code(v)                           # Primary Code (ICD-9)
    if col == 28:                                                  # Primary Code Description
        return is_str_nonempty(v) and not is_date(v) and not is_number(v)
    if col == 29: return is_icd10(v)                               # ICD-10 Code
    if col == 30:                                                  # ICD-10 Description
        return is_str_nonempty(v) and not is_date(v) and not is_number(v)

    # Financially Responsible (often blank — patient is responsible party)
    if col == 31:                                                  # FR Last Name
        return is_str_nonempty(v) and not is_date(v) and not is_number(v) \
            and len(_s(v)) <= 60
    if col == 32:                                                  # FR Name
        return is_str_nonempty(v) and not is_date(v) and not is_number(v) \
            and len(_s(v)) <= 80
    if col == 33: return is_int_in(v, 1, 99_999_999)               # FR Patient ID

    # Primary Insurance 34-37
    if 34 <= col <= 37: return is_ins_string(v)
    if col == 38: return is_policy_num(v)                          # Primary Policy #

    # Secondary Insurance 39-42
    if 39 <= col <= 42: return is_ins_string(v)
    if col == 43: return is_policy_num(v)                          # Secondary Policy #

    # Address
    if col == 44:                                                  # Addr Line 1
        return is_str_nonempty(v) and not is_state(v) and not is_email(v)
    if col == 45:                                                  # Addr Line 2 (often blank)
        # If next is a credit-amount or city, this col is blank
        if val_idx + 1 < len(vals) and is_number(vals[val_idx + 1]):
            return False  # Credit fields are next; Addr2 is blank, this is City
        if is_state(v) or is_zip(v) or is_email(v):
            return False
        return is_str_nonempty(v)
    if col == 46:                                                  # City
        if is_state(v) or is_zip(v):
            return False
        if isinstance(v, (int, float)) and not pd.isna(v):
            return False
        return is_str_nonempty(v)

    # Credit fields — usually 0 (numeric)
    if 47 <= col <= 50: return is_number(v) and not is_date(v)

    if col == 51: return is_date(v)                                # DOB
    if col == 52: return is_email(v)                               # EMail
    if col == 53:                                                  # Patient Name (Last, First)
        s = _s(v)
        return "," in s and 3 <= len(s) <= 80
    if col == 54: return is_state(v)                               # State
    if col == 55: return is_zip(v)                                 # Zip

    # Payment Applied 56-59
    if 56 <= col <= 59: return is_number(v) and not is_date(v)

    if col == 60:                                                  # Procedure Description
        return is_str_nonempty(v) and not is_date(v) and not is_number(v)
    if col == 61:                                                  # Modifiers (often blank/0)
        return is_modifier(v)
    if col == 62: return is_number(v) and not is_date(v)           # Procedure Charge

    if col == 63: return is_npi(v)                                 # Billable NPI
    if col == 64: return is_npi(v)                                 # Rendering NPI

    if col == 65:                                                  # Primary Care Provider
        return is_provider_name(v) or _s(v).startswith("No ")
    if col == 66: return is_visit_id(v)                            # VisitID
    if col == 67: return is_phone(v)                               # Phone Primary
    if col == 68: return is_sex(v)                                 # Sex

    return True


# ────────────── row + dataframe fix ──────────────

@dataclass
class FixReport:
    total_rows: int
    rows_realigned: int
    unresolved_rows: int
    unresolved_samples: List[dict]
    shifts_detected: bool


def fix_row(row: list) -> tuple[list, list]:
    """Realign one row using validators. Returns (fixed_list_NCOLS, leftover)."""
    row = list(row[:NCOLS])

    # Cols 0-4 always present (Patient ID, First, Last, DOS, CPT)
    fixed: list = [None] * NCOLS
    for i in range(5):
        fixed[i] = row[i]

    # Walk from col 5 onward.
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


def fix_dataframe(df: pd.DataFrame):
    """Returns (fixed_df, report_list, rows_realigned_count)."""
    out: list = []
    report: list = []
    rows_realigned = 0
    for i, row in df.iterrows():
        if i == 0:
            out.append(list(row[:NCOLS]))
            continue
        raw = row.tolist()
        # Detect shift via blank cells anywhere in the row
        if any(_isblank(v) for v in raw[:NCOLS]):
            rows_realigned += 1
        fixed, leftover = fix_row(raw)
        out.append(fixed)
        if leftover:
            report.append({
                "row_idx": int(i),
                "leftover_count": len(leftover),
                "leftover": leftover[:10],
            })
    return pd.DataFrame(out), report, rows_realigned


def _drop_phantom_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop columns whose header (row 0) is NaN/blank — PrimeSuite's report
    builder occasionally inserts spacer columns. Returns (df, n_dropped)."""
    if df.empty:
        return df, 0
    header = df.iloc[0]
    phantom = [i for i in range(df.shape[1]) if _isblank(header.iloc[i])]
    if not phantom:
        return df, 0
    keep = [i for i in range(df.shape[1]) if i not in phantom]
    return df.iloc[:, keep].reset_index(drop=True), len(phantom)


def fix_file(src_path: str, dst_path: str) -> FixReport:
    df = pd.read_excel(src_path, sheet_name=0, header=None)
    df, phantom_dropped = _drop_phantom_columns(df)
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
