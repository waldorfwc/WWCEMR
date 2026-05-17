"""One-off: normalize Surgery.patient_name / first_name / last_name to
Title Case.

  "MENYON ELIZABETH KEYS"  → "Menyon Elizabeth Keys"
  "monique wilson"         → "Monique Wilson"
  "Sophia hursey"          → "Sophia Hursey"
  "Smith, Jane"            → "Smith, Jane"  (already correct, no change)
  "o'brien"                → "O'Brien"
  "smith-jones"            → "Smith-Jones"

Note: "Mc" and "Mac" prefixes are NOT special-cased — they title-case to
"Mcdonald" / "Macgregor". Spot-fix those manually if any patients have
them; the script reports the before/after so you can find them.

Run with no flag = dry run. Run with --apply to commit changes.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal, init_db
from app.models.surgery import Surgery


# Tokens to leave uppercase (Roman numerals up to X for suffixes like "John Smith III")
_KEEP_UPPER = {"II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}


def title_word(word: str) -> str:
    """Title-case a word that's fully UPPER or fully lower. Words with
    intentional internal capitals (LaToria, ReEdna, McDonagh, O'Brien) are
    left untouched — they're deliberate.

    Apostrophe handling: 'o'brien' → 'O'Brien' (capitalises letter after the
    apostrophe too), but only when the input is fully lowercase.
    """
    if not word:
        return word
    if word.upper() in _KEEP_UPPER:
        return word.upper()
    # Strip apostrophes / hyphens for the all-upper / all-lower test
    alpha_only = re.sub(r"[^A-Za-z]", "", word)
    if not alpha_only:
        return word
    if not (alpha_only.isupper() or alpha_only.islower()):
        # Mixed case — preserve as-is
        return word
    # Normalise the casing piece-by-piece across hyphens and apostrophes
    out = re.sub(
        r"[A-Za-z]+",
        lambda m: m.group(0).capitalize(),
        word,
    )
    # After apostrophe: capitalise the next letter (O'Brien, D'Amico)
    out = re.sub(
        r"(?<=[A-Za-z]\')([a-z])",
        lambda m: m.group(1).upper(),
        out,
    )
    return out


def normalize_name(s):
    if not s:
        return s
    # Split on whitespace + comma (preserving delimiters)
    parts = re.split(r"(\s+|,)", s.strip())
    out = []
    for p in parts:
        if p is None:
            continue
        if p == "," or (p and p.isspace()):
            out.append(p)
        else:
            out.append(title_word(p))
    result = "".join(out)
    # Collapse any double-spaces introduced
    return re.sub(r"\s{2,}", " ", result).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                     help="Actually apply changes. Without it, just dry-runs.")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        rows = db.query(Surgery).all()
        changed = []
        for s in rows:
            new_pn = normalize_name(s.patient_name)
            new_fn = normalize_name(s.first_name)
            new_ln = normalize_name(s.last_name)
            pn_change = (new_pn or "") != (s.patient_name or "")
            fn_change = (new_fn or "") != (s.first_name or "")
            ln_change = (new_ln or "") != (s.last_name or "")
            if not (pn_change or fn_change or ln_change):
                continue
            changed.append((s, pn_change, new_pn, fn_change, new_fn, ln_change, new_ln))

        print(f"Surgeries scanned: {len(rows)}")
        print(f"Would change: {len(changed)}\n")

        for s, pc, npn, fc, nfn, lc, nln in changed[:200]:
            print(f"  {s.chart_number} :")
            if pc:
                print(f"    name: {s.patient_name!r} → {npn!r}")
            if fc:
                print(f"    first: {s.first_name!r} → {nfn!r}")
            if lc:
                print(f"    last:  {s.last_name!r} → {nln!r}")
        if len(changed) > 200:
            print(f"  …and {len(changed) - 200} more")

        if not args.apply:
            print("\nDRY RUN — no changes applied. Re-run with --apply to commit.")
            return

        for s, pc, npn, fc, nfn, lc, nln in changed:
            if pc: s.patient_name = npn
            if fc: s.first_name = nfn
            if lc: s.last_name = nln
        db.commit()
        print(f"\n✓ Applied to {len(changed)} surgeries.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
