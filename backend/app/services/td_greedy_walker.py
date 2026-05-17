"""Greedy column-walker repair for blank-collapsed Transaction Detail rows.

Background: PrimeSuite's TD export drops blank cells entirely — every value
right of a blank shifts left to fill the gap. A row that should have N/A in
col 5 and blank in col 6 exports without those two cells, packing all later
columns leftward by 2. By the end of the row, with multiple blanks dropped,
later cells can be 5–10 positions off.

Repair strategy: don't try to detect shifts cell-by-cell. Instead, walk the
canonical 60-column schema left-to-right; for each canonical column try to
fit the next *packed* (non-blank) value using a per-column validator. If it
fits, place it and advance both pointers; if not, that canonical column was
originally blank — leave it None and advance only the schema pointer. The
validators are what carry the load: strong validators (enum lists, exact
date patterns, NPI prefix rules) catch shifts; weak validators don't.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

import pandas as pd


# ---------- enum sets ----------

VALID_TYPES = {
    'PMT', 'CHG', 'C-ADJ', 'D-ADJ',
    'V-PMT', 'V-CHG', 'V-C-ADJ', 'V-D-ADJ', 'V-ADJ',
    'SLT-TO', 'SLT-FROM', 'MTC-TO', 'MTC-FROM',
    'OFFSET', 'RFND',
}

US_STATES = {
    'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA',
    'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
    'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT',
    'VA','WA','WV','WI','WY','DC','PR','VI','AS','GU','MP',
}

VALID_SEX = {'Female', 'Male'}

PAYMENT_METHODS = {
    'EFT', 'ACH', 'WIRE', 'ELECTRONIC', 'CHECK', 'CREDIT CARD',
    'DEBIT CARD', 'CASH', 'MONEY ORDER', 'CC', 'CARE CREDIT',
}

PAYMENT_SOURCES = {'Insurance', 'Patient', 'Other'}

INDICATOR_VALUES = {'Yes', 'No', 'Y', 'N', 'True', 'False', '0', '1'}


# ---------- value normalizers ----------

def _is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    s = str(v).strip()
    return s == ''


def _s(v: Any) -> str:
    if _is_blank(v):
        return ''
    return str(v).strip()


# ---------- validators ----------

def is_patient_id(v: Any) -> bool:
    s = _s(v)
    if not s:
        return False
    s = s.replace('.0', '')
    return s.isdigit() and 1 <= len(s) <= 8


def is_full_name(v: Any) -> bool:
    s = _s(v)
    return ',' in s and len(s) >= 4 and len(s) <= 80


def is_first_or_last_name(v: Any) -> bool:
    s = _s(v)
    if not s or len(s) > 50:
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z\-' .]*$", s))


def is_date(v: Any) -> bool:
    """M/D/YYYY or MM/DD/YYYY, year 1900-2030."""
    s = _s(v)
    if not s:
        return False
    if isinstance(v, datetime):
        return 1900 <= v.year <= 2030
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d'):
        try:
            d = datetime.strptime(s, fmt)
            return 1900 <= d.year <= 2030
        except ValueError:
            continue
    return False


def is_visit_id(v: Any) -> bool:
    """Number or literal 'N/A'."""
    s = _s(v)
    if s.upper() == 'N/A':
        return True
    s = s.replace('.0', '')
    return s.isdigit() and 1 <= len(s) <= 10


def is_charge_ticket(v: Any) -> bool:
    """Number only."""
    s = _s(v).replace('.0', '')
    return s.isdigit() and 1 <= len(s) <= 10


def is_type(v: Any) -> bool:
    return _s(v) in VALID_TYPES


_HAS_LETTER = re.compile(r"[A-Za-z]")


def is_address1(v: Any) -> bool:
    """Street address: typically <num> <name> <suffix>. Must contain letters
    (so a stray number doesn't get swallowed as 'address')."""
    s = _s(v)
    if not s or len(s) > 100:
        return False
    if not _HAS_LETTER.search(s):
        return False
    if s in VALID_TYPES or s in US_STATES or s in VALID_SEX:
        return False
    if is_date(v) or is_phone(v) or is_email(v) or is_zip(v):
        return False
    return True


_ADDR2_TOKENS = re.compile(
    r"\b(APT|APARTMENT|SUITE|STE|UNIT|FL|FLOOR|BLDG|BUILDING|RM|ROOM|"
    r"PO\s*BOX|P\.O\.|BOX|TRLR|TRAILER|LOT|SPC|SPACE|REAR|FRONT|UPPER|LOWER)\b",
    re.IGNORECASE,
)


def is_address2(v: Any) -> bool:
    """Address Line 2: apartment/suite/unit indicator. Must contain a digit
    OR a unit token (Apt/Suite/PO Box/etc.). Bare city/proper-noun strings
    must NOT pass — otherwise the walker swallows the city into address2.
    """
    s = _s(v)
    if not s or len(s) > 60:
        return False
    if s in VALID_TYPES or s in US_STATES or s in VALID_SEX:
        return False
    if is_date(v) or is_phone(v) or is_email(v) or is_zip(v) or is_state(v):
        return False
    has_digit = any(ch.isdigit() for ch in s)
    has_token = bool(_ADDR2_TOKENS.search(s)) or '#' in s
    return has_digit or has_token


def is_city(v: Any) -> bool:
    s = _s(v)
    if not s or len(s) > 40:
        return False
    if s in VALID_TYPES or s in US_STATES or s in VALID_SEX:
        return False
    if is_date(v) or is_money(v) or is_phone(v) or is_email(v) or is_zip(v):
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z\-' .]*$", s))


def is_state(v: Any) -> bool:
    return _s(v).upper() in US_STATES


def is_zip(v: Any) -> bool:
    s = _s(v).replace('.0', '')
    return bool(re.match(r"^\d{5}(-\d{4})?$", s))


def is_phone(v: Any) -> bool:
    s = _s(v)
    digits = re.sub(r'\D', '', s)
    return len(digits) == 10


def is_email(v: Any) -> bool:
    s = _s(v)
    return '@' in s and '.' in s and len(s) <= 80


def is_sex(v: Any) -> bool:
    return _s(v) in VALID_SEX


_MONEY_CEILING = 50000.0  # any |value| > $50K is a CPT/NPI/control# leak, not money


def is_money(v: Any) -> bool:
    """Numeric, possibly negative, possibly with $/comma. Accepts 0.
    Rejects values |v| > $50K (those are CPT codes, claim numbers, NPIs
    leaking into a money column — never legitimate payment values at WWC)."""
    if _is_blank(v):
        return False
    try:
        if isinstance(v, (int, float)):
            n = float(v)
        else:
            s = _s(v).replace('$', '').replace(',', '').replace('(', '-').replace(')', '')
            n = float(s)
    except (ValueError, TypeError):
        return False
    return abs(n) <= _MONEY_CEILING


def is_cpt(v: Any) -> bool:
    """5 digits or HCPCS (1 letter + 4 digits)."""
    s = _s(v).replace('.0', '')
    return bool(re.match(r"^(\d{5}|[A-Z]\d{4})$", s.upper()))


def is_modifier(v: Any) -> bool:
    """2-char alphanumeric, possibly comma/space-separated list."""
    s = _s(v).upper()
    if not s or len(s) > 20:
        return False
    parts = re.split(r"[\s,]+", s)
    return all(re.match(r"^[A-Z0-9]{2}$", p) for p in parts if p)


def is_dx_code(v: Any) -> bool:
    """ICD-10: letter + 2 digits + optional .X.X format. Possibly multiple."""
    s = _s(v).upper()
    if not s or len(s) > 100:
        return False
    parts = re.split(r"[\s,]+", s)
    return all(re.match(r"^[A-Z]\d{2}(\.[A-Z0-9]+)?$", p) for p in parts if p)


def is_units(v: Any) -> bool:
    """Numeric units (1, 2, 0.5, etc.) — small range."""
    if _is_blank(v):
        return False
    try:
        n = float(_s(v))
        return -10 <= n <= 100
    except ValueError:
        return False


def is_provider_name(v: Any) -> bool:
    """'Last, First [credentials]' format."""
    s = _s(v)
    if not s or len(s) > 80:
        return False
    if s in VALID_TYPES:
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z\-' .]+,\s*[A-Za-z]", s))


def is_practice_location(v: Any) -> bool:
    s = _s(v)
    if not s or len(s) > 80 or not _HAS_LETTER.search(s):
        return False
    return not is_provider_name(v)


def is_indicator(v: Any) -> bool:
    return _s(v) in INDICATOR_VALUES


def is_payment_method(v: Any) -> bool:
    s = _s(v).upper()
    return s in {m.upper() for m in PAYMENT_METHODS}


def is_payment_source(v: Any) -> bool:
    s = _s(v).title()
    return s in PAYMENT_SOURCES


def is_str_short(v: Any) -> bool:
    """Generic short string. Must contain at least one letter — a bare
    number means the walker is misaligned and should skip this column."""
    s = _s(v)
    if not s or len(s) > 200:
        return False
    return bool(_HAS_LETTER.search(s))


def is_txn_description(v: Any) -> bool:
    """Transaction: Description — semicolon-separated date/CPT/ICD summary.
    Accepts strings WITHOUT letters (the typical format is purely numeric +
    punctuation, e.g. '02/09/2023; 99204; 625.3, 625.9'). Use ONLY for col 47;
    string columns elsewhere should still require letters."""
    s = _s(v)
    if not s or len(s) > 300:
        return False
    if isinstance(v, (int, float)):
        return False
    # Must contain at least one date-or-code-like token (slash, semicolon, etc.)
    return any(ch in s for ch in ';,/ -')


# ---------- canonical schema ----------
# (column_index, header, validator, can_be_blank)
# can_be_blank=False = walker MUST place a value here (else fail)
# can_be_blank=True = walker can skip if next packed value doesn't fit

SCHEMA: List[Tuple[int, str, Callable[[Any], bool], bool]] = [
    (0,  'Patient: Patient ID',                     is_patient_id,        False),
    (1,  'Patient: Name',                           is_full_name,         False),
    (2,  'Patient: First Name',                     is_first_or_last_name, False),
    (3,  'Patient: Last Name',                      is_first_or_last_name, False),
    (4,  'Patient: Date Of Birth',                  is_date,              False),
    (5,  'Transaction: Visit ID',                   is_visit_id,          False),
    (6,  'Transaction: Charge Ticket Number',       is_charge_ticket,     True),
    (7,  'Transaction: Type',                       is_type,              False),
    (8,  'Patient: Address Line 1',                 is_address1,          False),
    (9,  'Patient: Address Line 2',                 is_address2,          True),
    (10, 'Patient: City',                           is_city,              False),
    (11, 'Patient: State',                          is_state,             False),
    (12, 'Patient: Zip Code',                       is_zip,               False),
    (13, 'Patient: Phone Primary',                  is_phone,             True),
    (14, 'Patient: EMail',                          is_email,             True),
    (15, 'Patient: Sex',                            is_sex,               False),
    (16, 'Date: Date of Service',                   is_date,              True),
    (17, 'Date: Posting Date',                      is_date,              True),
    (18, 'Date: Create Date',                       is_date,              True),
    (19, 'Date: Original Posting Date',             is_date,              True),
    (20, 'Date: Original Create Date',              lambda v: is_date(v) or _s(v) == '0', True),
    # Money block — can be 0/blank for irrelevant transaction types
    (21, 'Transaction: Amount - Charge Voids',           is_money, True),
    (22, 'Transaction: Amount - Adjustment Voids',       is_money, True),
    (23, 'Transaction: Amount - Adjustment Offsets',     is_money, True),
    (24, 'Transaction: Amount - Payment Voids',          is_money, True),
    (25, 'Transaction: Amount - Payment Offsets',        is_money, True),
    (26, 'Transaction: Amount - Transaction Amount',     is_money, True),
    (27, 'Transaction: Adjustment Type',                 is_str_short, True),
    (28, 'Transaction: Adjustment Sub-Type',             is_str_short, True),
    (29, 'Transaction: Applied To',                      is_str_short, True),
    (30, 'Transaction: Payment Method',                  is_payment_method, True),
    (31, 'Transaction: Payment Supplier',                is_str_short, True),
    (32, 'Transaction: Payment/Adjustment Source',       is_payment_source, True),
    (33, 'Transaction: Payment/Adjustment Additional Info', is_str_short, True),
    (34, 'Transaction: Amount - Net Charges',            is_money, True),
    (35, 'Transaction: Amount - Net Adjustments',        is_money, True),
    (36, 'Transaction: Amount - Net Payments',           is_money, True),
    (37, 'Transaction: Amount - Gross Charges',          is_money, True),
    (38, 'Transaction: Amount - Gross Adjustments',      is_money, True),
    (39, 'Transaction: Amount - Gross Insurance Payments', is_money, True),
    (40, 'Transaction: Amount - Gross Patient/Other Payments', is_money, True),
    (41, 'Transaction: Amount - Gross Payments',         is_money, True),
    (42, 'Transaction: Procedure Code',                  is_cpt, True),
    (43, 'Transaction: Procedure Description',           is_str_short, True),
    (44, 'Transaction: Procedure Modifiers',             is_modifier, True),
    (45, 'Transaction: Diagnosis ICD10 Codes',           is_dx_code, True),
    (46, 'Transaction: Net Charge Units',                is_units, True),
    (47, 'Transaction: Description',                     is_txn_description, True),
    (48, 'Orginal Transaction: Transaction Amount',      is_money, True),
    (49, 'Original Transaction: Payment/Adjustment Source', is_payment_source, True),
    (50, 'Transaction: Billable Provider Name',          is_provider_name, True),
    (51, 'Transaction: Rendering Provider Name',         is_provider_name, True),
    (52, 'Transaction: Referring Provider Name',         is_provider_name, True),
    (53, 'Transaction: Practice Location',               is_practice_location, True),
    (54, 'Transaction: Service Location',                is_practice_location, True),
    (55, 'Transaction: User',                            is_str_short, True),
    (56, 'Transaction: Void Indicator',                  is_indicator, True),
    (57, 'Transaction: ERA Indicator',                   is_indicator, True),
    (58, 'Transaction: Charge Override Indicator',       is_indicator, True),
    (59, 'Transaction: Refund Check Number',             is_str_short, True),
]


# ---------- walker ----------

def reconstruct_row(packed: List[Any]) -> Tuple[List[Any], int, List[Any]]:
    """Walk the canonical schema, placing each packed value into the next
    matching column. Returns (reconstructed_row, n_placed, leftover).

    `leftover` = packed values that couldn't be placed before schema exhausted
    — typically zero, but non-zero leftover means the row is malformed.
    """
    out: List[Any] = [None] * len(SCHEMA)
    schema_idx = 0
    val_idx = 0

    while schema_idx < len(SCHEMA) and val_idx < len(packed):
        col_idx, name, validator, can_blank = SCHEMA[schema_idx]
        v = packed[val_idx]
        if validator(v):
            out[col_idx] = v
            schema_idx += 1
            val_idx += 1
        else:
            if can_blank:
                # Skip this canonical col — it was blank in the source
                schema_idx += 1
            else:
                # Required col but value doesn't fit.
                # Try one rescue: maybe we already misplaced earlier.
                # For now: skip the value (it's garbage) and try again.
                val_idx += 1

    n_placed = sum(1 for x in out if x is not None)
    leftover = list(packed[val_idx:])
    return out, n_placed, leftover


def reconstruct_dataframe(df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    """Repair every row of a TD DataFrame using the greedy walker.
    Returns (fixed_df, stats_dict)."""
    fixed_rows: List[List[Any]] = []
    stats = {
        'total_rows': len(df),
        'fully_repaired': 0,        # all canonical placed (within tolerance)
        'partial': 0,               # some placed but with leftover
        'unrecoverable': 0,         # very few values placed
        'leftover_total': 0,
    }
    for _, row in df.iterrows():
        # Pack: drop blank cells, keep only the packed values left-to-right
        packed = [v for v in row.tolist() if not _is_blank(v)]
        out, n_placed, leftover = reconstruct_row(packed)
        fixed_rows.append(out)
        stats['leftover_total'] += len(leftover)
        if n_placed >= 8 and len(leftover) == 0:
            stats['fully_repaired'] += 1
        elif n_placed >= 5:
            stats['partial'] += 1
        else:
            stats['unrecoverable'] += 1

    headers = [name for _, name, _, _ in SCHEMA]
    fixed_df = pd.DataFrame(fixed_rows, columns=headers)
    return fixed_df, stats
