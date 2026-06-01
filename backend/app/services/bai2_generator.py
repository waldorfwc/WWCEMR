"""CSV parser + filter + BAI2 v2 file generator."""
from __future__ import annotations

import csv as _csv
import hashlib
import io
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.models.bai2 import Bai2Import, Bai2Transaction


# ─────────────────────────────────────────────────────────────────────
# Payer name expansion — bank-descriptor → ModMed-friendly insurance name

PAYER_EXPANSION = [
    (r'\bGHMSI FEP NON-PO',           'BCBS Carefirst FEP (GHMSI Non-Postal)'),
    (r'\bGHMSI FEP POSTAL',           'BCBS Carefirst FEP (GHMSI Postal)'),
    (r'\bCFMI FEP NON-POS',           'BCBS Carefirst FEP (CFMI Non-Postal)'),
    (r'\bCFMI FEP POSTAL',            'BCBS Carefirst FEP (CFMI Postal)'),
    (r'\bCFBC FEP NON-POS',           'BCBS Carefirst BlueChoice FEP (Non-Postal)'),
    (r'\bCFBC FEP POSTAL',            'BCBS Carefirst BlueChoice FEP (Postal)'),
    (r'\bCAREFIRST BLUECH',           'BCBS Carefirst BlueChoice'),
    (r'\bCAREFIRST OF MD',            'BCBS Carefirst of Maryland'),
    (r'\bCAREFIRST GHMSI',            'BCBS Carefirst GHMSI'),
    (r'\bUHC OF THE MIDAT',           'UHC of the Mid-Atlantic'),
    (r'\bUNITEDHEALTHCARE',           'UnitedHealthcare'),
    (r'\bOPTIMUM CHOICE I MD',        'UHC Optimum Choice Maryland'),
    (r'\bOPTIMUM CHOICE',             'UHC Optimum Choice'),
    (r'\bFREEDOM LIFE INS',           'UHC Freedom Life Insurance'),
    (r'\bGOLDEN RULE INSU',           'UHC Golden Rule Insurance'),
    (r'\bAETNA A04',                  'Aetna A04'),
    (r'\bAETNA AS01',                 'Aetna AS01'),
    (r'\bAETNA',                      'Aetna'),
    (r'\bHNB - ECHO',                 'ECHO Health (payer routing)'),
    (r'\bPAY PLUS',                   'PayPlus/Zelis (payer routing)'),
    (r'\bMERCHANT BNKCD',             'Merchant Bankcard Settlement'),
    (r'\bTRICARE',                    'Tricare'),
    (r'\bAMERIGROUP',                 'Wellpoint Amerigroup'),
    (r'\bWELLPOINT',                  'Wellpoint'),
    (r'\bMEDSTAR',                    'Medstar Family Choice'),
    (r'\bPRIORITY PARTNERS',          'Priority Partners'),
    (r'\bCIGNA',                      'Cigna'),
    (r'\bHUMANA',                     'Humana'),
    (r'\bKAISER',                     'Kaiser'),
    (r'\bUMR',                        'UHC UMR'),
    (r'\bMEDICARE',                   'Medicare'),
]


def _expand_payer(s: str) -> str:
    su = s.upper()
    for pat, name in PAYER_EXPANSION:
        if re.search(pat, su):
            return name
    return s


# ─────────────────────────────────────────────────────────────────────
# CSV row parsing helpers

def _parse_amount(s: Any) -> float:
    s = str(s or '').strip()
    sign = -1 if s.startswith('-') else 1
    digits = re.sub(r'[^\d.]', '', s)
    try:
        return sign * float(digits) if digits else 0.0
    except ValueError:
        return 0.0


def _parse_date(s: Any) -> Optional[date]:
    """Handles 'PENDING - 05/05/2026', '05/05/2026', '5/5/26', '2026-05-05'."""
    if not s:
        return None
    s = str(s).strip()
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', s)
    if m:
        y = int(m.group(3))
        if y < 100:
            y += 2000
        return date(y, int(m.group(1)), int(m.group(2)))
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _parse_description(desc: str) -> Tuple[str, str, str]:
    """Returns (expanded_company, method, last_4)."""
    s = (desc or '').strip()
    m = re.search(r'(?:x{2,})(\d{4,})\s*$', s, re.IGNORECASE)
    last_4 = m.group(1)[-4:] if m else ''
    if m:
        s = s[: m.start()].rstrip()
    su = s.upper()
    if 'WIRE' in su:
        method = 'WIRE'
    elif 'CHECK' in su or re.search(r'\bCHK\b', su):
        method = 'CHECK'
    elif 'ACH' in su:
        method = 'ACH'
    else:
        method = 'ACH'
    s = re.sub(r'^ACH\s+(DEP|DEBIT|CREDIT)\s+', '', s, flags=re.IGNORECASE)
    s = re.sub(r'^DEPOSIT\s+', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+HCCLAIMPMT(\s+CORPORATE)?(\s+ACH)?\s*$', '', s, flags=re.IGNORECASE)
    return _expand_payer(s.strip()), method, last_4


def _bai_type_code(method: str) -> str:
    return {'ACH': '195', 'CHECK': '165', 'WIRE': '252'}.get(method, '195')


def _cents(amt: float) -> str:
    return str(int(round(float(amt) * 100)))


def _yymmdd(d: date) -> str:
    return d.strftime('%y%m%d')


# ─────────────────────────────────────────────────────────────────────
# Filter rules

# Patterns that are ALWAYS dropped regardless of filter toggles.
# These are not insurance payers — including them in BAI2 would just
# confuse the ModMed reconciliation match.
ALWAYS_DROP_PATTERNS = [
    r'\bMERCHANT\s+BNKCD\b',     # merchant bank-card processor settlement
]


@dataclass
class FilterOptions:
    skip_withdrawals: bool = True
    skip_modmed: bool = True
    skip_stripe: bool = True
    skip_zero: bool = True


# ─────────────────────────────────────────────────────────────────────
# CSV parsing + filtering — pure function, no DB

@dataclass
class ParsedTransaction:
    transaction_date: date
    description: str
    formatted_text: str
    amount: float
    last_4: str
    method: str
    bai_type_code: str
    dedup_key: str


@dataclass
class ParseResult:
    transactions: List[ParsedTransaction] = field(default_factory=list)
    csv_row_count: int = 0
    skipped_withdrawal: int = 0
    skipped_modmed: int = 0
    skipped_stripe: int = 0
    skipped_zero: int = 0
    skipped_duplicate_in_file: int = 0
    skipped_always_drop: int = 0


def parse_csv_from_bytes(body: bytes, filters: FilterOptions) -> ParseResult:
    """Parse a bank CSV from raw bytes. Apply filters + within-file dedup.
    No DB writes. Same semantics as parse_csv(path, …) but doesn't touch
    the filesystem."""
    import io
    result = ParseResult()
    text = body.decode("utf-8-sig", errors="replace")
    reader = _csv.DictReader(io.StringIO(text))
    rows = list(reader)
    result.csv_row_count = len(rows)
    return _filter_rows(rows, filters, result)


def parse_csv(file_path: str, filters: FilterOptions) -> ParseResult:
    """Path-based wrapper around parse_csv_from_bytes. Kept for any
    callers that still have a filesystem path; new code should pass
    bytes via parse_csv_from_bytes."""
    with open(file_path, 'rb') as f:
        return parse_csv_from_bytes(f.read(), filters)


def _filter_rows(rows, filters: FilterOptions,
                    result: ParseResult) -> ParseResult:
    """Shared per-row loop used by parse_csv_from_bytes / parse_csv."""
    seen_keys: set[str] = set()
    today = date.today()
    for r in rows:
        # Auto-detect column names (case-insensitive)
        desc = ''
        for k in r.keys():
            if k and 'description' in k.lower(): desc = (r[k] or '').strip(); break
        amt_raw = ''
        for k in r.keys():
            if k and 'amount' in k.lower() and 'description' not in k.lower():
                amt_raw = r[k]; break
        date_raw = ''
        for k in r.keys():
            if k and 'date' in k.lower():
                date_raw = r[k]; break

        amt = _parse_amount(amt_raw)
        dt = _parse_date(date_raw) or today
        du = desc.upper()

        if filters.skip_zero and amt == 0:
            result.skipped_zero += 1
            continue
        if filters.skip_withdrawals and amt < 0:
            result.skipped_withdrawal += 1
            continue
        if filters.skip_modmed and ('MODMED' in du or 'MODERNIZING MEDICINE' in du):
            result.skipped_modmed += 1
            continue
        if filters.skip_stripe and 'STRIPE' in du:
            result.skipped_stripe += 1
            continue
        # Hardcoded always-drop list (e.g. merchant bankcard — not a payer)
        if any(re.search(pat, du) for pat in ALWAYS_DROP_PATTERNS):
            result.skipped_always_drop += 1
            continue

        company, method, last_4 = _parse_description(desc)
        formatted = f"{company} {method}" + (f" x{last_4}" if last_4 else "")
        key = hashlib.sha256(f'{dt}|{amt:.2f}|{formatted}'.encode()).hexdigest()

        if key in seen_keys:
            result.skipped_duplicate_in_file += 1
            continue
        seen_keys.add(key)

        result.transactions.append(ParsedTransaction(
            transaction_date=dt,
            description=desc,
            formatted_text=formatted,
            amount=amt,
            last_4=last_4,
            method=method,
            bai_type_code=_bai_type_code(method),
            dedup_key=key,
        ))
    return result


# ─────────────────────────────────────────────────────────────────────
# BAI2 file generation

def _sanitize_bai_field(s) -> str:
    """Make a string safe to embed in a BAI2 record value.

    BAI2 record framing: fields are comma-separated, records end with '/',
    one record per LINE. So we strip newlines, tabs, carriage returns
    (which would orphan the rest of the description as a phantom record
    in downstream parsers like ModMed's payment-import) and replace
    commas / slashes with safe substitutes. Also collapses runs of
    whitespace so descriptions stay compact.
    """
    if s is None:
        return ''
    s = str(s)
    s = s.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    s = s.replace(',', ' ').replace('/', '-')
    return ' '.join(s.split())


def render_bai2(transactions: List[ParsedTransaction], bank_name: str,
                account_full: Optional[str], account_last_4: str) -> str:
    """Generate the BAI v2 file as a string. Groups transactions by date —
    one 02/98 group per posting date so multi-day CSVs land cleanly."""
    if not transactions:
        return ''

    sender = bank_name or 'BANK'
    receiver = 'WWC'
    account_id = (account_full or f'x{account_last_4}' if account_last_4 else '[ACCT]')

    by_date: Dict[date, List[ParsedTransaction]] = defaultdict(list)
    for t in transactions:
        by_date[t.transaction_date].append(t)

    file_total_cents = sum(int(round(t.amount * 100)) for t in transactions)
    today = date.today()

    out: List[str] = []
    out.append(f'01,{sender},{receiver},{_yymmdd(today)},0900,000001,,,2/')
    record_total = 1
    group_count = 0

    for d in sorted(by_date.keys()):
        txns = by_date[d]
        group_total = sum(int(round(t.amount * 100)) for t in txns)
        out.append(f'02,{receiver},{sender},1,{_yymmdd(d)},,,USD,2/')
        out.append(f'03,{account_id},USD,015,{group_total}/')
        records_in_group = 2  # 02 + 03
        for t in txns:
            text = _sanitize_bai_field(t.formatted_text)
            # Default to '475' (Misc Credit) if the type code is missing.
            # Better a generic-but-valid code than literal 'None' in the file.
            type_code = (str(t.bai_type_code).strip() if t.bai_type_code else '') or '475'
            out.append(f'16,{type_code},{_cents(t.amount)},Z,,,{text}/')
            records_in_group += 1
        out.append(f'49,{group_total},{records_in_group - 1}/')   # records since 02 exclusive
        records_in_group += 1
        out.append(f'98,{group_total},1,{records_in_group}/')
        record_total += records_in_group
        group_count += 1

    record_total += 1  # for the upcoming 99
    out.append(f'99,{file_total_cents},{group_count},{record_total}/')

    return '\n'.join(out) + '\n'


def make_filename(bank_name: str, start: date, end: date) -> str:
    """`PNC x395 26.05.01 - 26.05.05.bai` (single day: `PNC x395 26.05.05.bai`)."""
    bn = (bank_name or 'BANK').strip()
    if start == end:
        return f'{bn} {start.strftime("%y.%m.%d")}.bai'
    return f'{bn} {start.strftime("%y.%m.%d")} - {end.strftime("%y.%m.%d")}.bai'


# ─────────────────────────────────────────────────────────────────────
# Top-level: import a CSV and produce a Bai2Import record

def process_csv_to_bai2(
    db: Session, csv_path: str, csv_original_name: str,
    bank_name: str, account_last_4: str, account_full: Optional[str],
    filters: FilterOptions, generated_by: Optional[str],
) -> Bai2Import:
    """1) parse the CSV  2) cross-import dedup against existing transactions
    3) save Bai2Import + Bai2Transaction rows  4) write the BAI2 file to disk."""
    parsed = parse_csv(csv_path, filters)

    # Cross-import dedup — drop any txn whose key already exists in DB
    keys_in_file = [t.dedup_key for t in parsed.transactions]
    if keys_in_file:
        existing = {
            row[0]
            for row in db.query(Bai2Transaction.dedup_key)
            .filter(Bai2Transaction.dedup_key.in_(keys_in_file)).all()
        }
    else:
        existing = set()

    new_txns = [t for t in parsed.transactions if t.dedup_key not in existing]
    skipped_prior = len(parsed.transactions) - len(new_txns)

    if not new_txns:
        # Nothing new to write. Still record the import for the audit trail.
        imp = Bai2Import(
            csv_filename=csv_original_name, csv_path=csv_path,
            bank_name=bank_name, account_last_4=account_last_4, account_full=account_full,
            csv_row_count=parsed.csv_row_count,
            transactions_included=0,
            skipped_withdrawal=parsed.skipped_withdrawal,
            skipped_modmed=parsed.skipped_modmed,
            skipped_stripe=parsed.skipped_stripe,
            skipped_zero=parsed.skipped_zero,
            skipped_duplicate_in_file=parsed.skipped_duplicate_in_file,
            skipped_prior_imports=skipped_prior,
            total_amount=Decimal('0'),
            generated_by=generated_by,
            notes='No new transactions — all rows were duplicates of prior imports.',
        )
        db.add(imp); db.commit(); db.refresh(imp)
        return imp

    # Compute date range
    dates = [t.transaction_date for t in new_txns]
    start, end = min(dates), max(dates)
    bai2_text = render_bai2(new_txns, bank_name, account_full, account_last_4)
    filename = make_filename(bank_name, account_last_4, start, end)

    out_dir = os.path.join(settings.upload_dir, 'bai2_files')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, filename)
    # If a file with the same name exists, append an index
    if os.path.exists(out_path):
        base, ext = os.path.splitext(filename)
        i = 2
        while os.path.exists(out_path):
            out_path = os.path.join(out_dir, f'{base} ({i}){ext}')
            i += 1
        filename = os.path.basename(out_path)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(bai2_text)

    total_amount = Decimal(str(sum(t.amount for t in new_txns)))

    imp = Bai2Import(
        csv_filename=csv_original_name, csv_path=csv_path,
        bank_name=bank_name, account_last_4=account_last_4, account_full=account_full,
        bai2_filename=filename, bai2_path=out_path,
        date_range_start=start, date_range_end=end,
        csv_row_count=parsed.csv_row_count,
        transactions_included=len(new_txns),
        skipped_withdrawal=parsed.skipped_withdrawal,
        skipped_modmed=parsed.skipped_modmed,
        skipped_stripe=parsed.skipped_stripe,
        skipped_zero=parsed.skipped_zero,
        skipped_duplicate_in_file=parsed.skipped_duplicate_in_file,
        skipped_prior_imports=skipped_prior,
        total_amount=total_amount,
        generated_by=generated_by,
    )
    db.add(imp); db.flush()

    for t in new_txns:
        db.add(Bai2Transaction(
            import_id=imp.id,
            transaction_date=t.transaction_date,
            description=t.description,
            formatted_text=t.formatted_text,
            amount=Decimal(str(t.amount)),
            last_4=t.last_4 or None,
            method=t.method,
            bai_type_code=t.bai_type_code,
            dedup_key=t.dedup_key,
        ))
    db.commit(); db.refresh(imp)
    return imp
