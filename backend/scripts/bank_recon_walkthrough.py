#!/usr/bin/env python
"""Bank-reconciliation walk-through / diagnostic on real bank CSV files.

Exercises the SAME parser + identity logic that POST /bank-recon/preview and
/generate use, against real files on disk — no DB writes, nothing sent
anywhere. Two modes:

  # One file → pending/filter/final breakdown
  python -m scripts.bank_recon_walkthrough uploads/bai2_csv/<a>.csv

  # Two files → "import A then B" identity-dedup analysis (what B would
  # auto-exclude as a true date+amount+last4 duplicate vs import as new,
  # and how many genuinely-new B rows fall inside A's date range — the ones
  # the retired date-range coverage rule would have silently dropped)
  python -m scripts.bank_recon_walkthrough <A>.csv <B>.csv

Run from the backend/ dir with the venv active. Used to verify the
2026-06-16 bank-recon fix (drop pending; identity-based exclusion).
"""
import sys
from decimal import Decimal

from app.services.bai2_generator import (
    parse_csv_from_bytes, FilterOptions, _is_pending,
)


def _q2(a):
    return Decimal(str(a if a is not None else 0)).quantize(Decimal("0.01"))


def _identity(t):
    return (t.transaction_date, _q2(t.amount), t.last_4 or "")


def _date_cell(row):
    for k in row:
        if k and "date" in k.lower():
            return row[k]
    return ""


def single_file(path):
    import csv as _csv
    import io
    raw = open(path, "rb").read()
    rows = list(_csv.DictReader(io.StringIO(raw.decode("utf-8-sig", "replace"))))
    pending = [r for r in rows if _is_pending(r, _date_cell(r))]
    res = parse_csv_from_bytes(raw, FilterOptions())
    dates = [t.transaction_date for t in res.transactions]
    filtered = (res.skipped_withdrawal + res.skipped_modmed + res.skipped_stripe
                + res.skipped_zero + res.skipped_duplicate_in_file
                + res.skipped_always_drop)
    print(f"── {path.split('/')[-1]} ({res.csv_row_count} rows) ──")
    print(f"  PENDING dropped (uncounted): {len(pending)}")
    print(f"  filtered: withdrawal={res.skipped_withdrawal} modmed={res.skipped_modmed} "
          f"stripe={res.skipped_stripe} zero={res.skipped_zero} "
          f"dup_in_file={res.skipped_duplicate_in_file} always_drop={res.skipped_always_drop}")
    print(f"  FINAL importable: {len(res.transactions)}")
    if dates:
        print(f"  date range: {min(dates)}..{max(dates)}")
    print(f"  check: {res.csv_row_count} = {len(pending)} pending + {filtered} filtered "
          f"+ {len(res.transactions)} final")
    return res


def pair(a_path, b_path):
    A = parse_csv_from_bytes(open(a_path, "rb").read(), FilterOptions())
    B = parse_csv_from_bytes(open(b_path, "rb").read(), FilterOptions())
    aD = [t.transaction_date for t in A.transactions]
    bD = [t.transaction_date for t in B.transactions]
    if not aD or not bD:
        print("one of the files has no final transactions"); return
    a_lo, a_hi = min(aD), max(aD)
    prior = {_identity(t) for t in A.transactions}
    dup = [t for t in B.transactions if _identity(t) in prior]
    new = [t for t in B.transactions if _identity(t) not in prior]
    in_range_new = [t for t in new if a_lo <= t.transaction_date <= a_hi]
    print(f"── import A then B ──")
    print(f"  A {a_path.split('/')[-1][:8]}: {a_lo}..{a_hi}  final={len(A.transactions)}")
    print(f"  B {b_path.split('/')[-1][:8]}: {min(bD)}..{max(bD)}  final={len(B.transactions)}")
    print(f"  B auto-excluded as true duplicate (date+amount+last4 in A): {len(dup)}")
    print(f"  B imported as new (no identity match):                      {len(new)}")
    print(f"  └─ of those, inside A's range [{a_lo}..{a_hi}]:              {len(in_range_new)}")
    print(f"     (retired date-coverage rule would have dropped these {len(in_range_new)})")
    for t in in_range_new[:10]:
        print(f"        {t.transaction_date}  ${float(t.amount):>10,.2f}  "
              f"x{t.last_4 or '----'}  {t.formatted_text[:40]}")


def main(argv):
    args = argv[1:]
    if len(args) == 1:
        single_file(args[0])
    elif len(args) == 2:
        pair(args[0], args[1])
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
