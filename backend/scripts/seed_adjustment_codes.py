"""Seed the CARC + RARC reference table with common codes, then (optionally)
enrich each new row with LLM-generated plain-English + fix guidance.

Idempotent:
  - Rows are upserted by (code_type, code); official_verbiage is refreshed.
  - plain_english / how_to_fix are only generated for rows that don't have
    them yet (or when --reenrich is passed).

Run:
  python scripts/seed_adjustment_codes.py              # seed + enrich new rows
  python scripts/seed_adjustment_codes.py --no-enrich  # just seed, skip LLM
  python scripts/seed_adjustment_codes.py --reenrich   # force re-run LLM on all
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

from app.database import SessionLocal, init_db
from app.models.adjustment_code_reference import AdjustmentCodeReference
from app.services.adjustment_code_seed_data import CARC_CODES, RARC_CODES


def upsert_codes(db) -> tuple[int, int]:
    """Return (inserted, updated) counts."""
    inserted = updated = 0
    for code_type, rows in (("CARC", CARC_CODES), ("RARC", RARC_CODES)):
        for code, verbiage in rows:
            existing = db.query(AdjustmentCodeReference).filter(
                AdjustmentCodeReference.code_type == code_type,
                AdjustmentCodeReference.code == code,
            ).first()
            if existing:
                if existing.official_verbiage != verbiage:
                    existing.official_verbiage = verbiage
                    updated += 1
                continue
            db.add(AdjustmentCodeReference(
                code_type=code_type,
                code=code,
                official_verbiage=verbiage,
            ))
            inserted += 1
    db.commit()
    return inserted, updated


def enrich(db, reenrich: bool) -> tuple[int, int]:
    """Run the LLM enricher on rows that don't yet have plain_english.
    Returns (enriched, skipped) counts. Skipped = already had enrichment.
    """
    from app.services.adjustment_code_enricher import enrich_code

    q = db.query(AdjustmentCodeReference)
    if not reenrich:
        q = q.filter(AdjustmentCodeReference.plain_english.is_(None))
    rows = q.order_by(
        AdjustmentCodeReference.code_type, AdjustmentCodeReference.code
    ).all()

    enriched = 0
    for r in rows:
        try:
            enr = enrich_code(r.code_type, r.code, r.official_verbiage)
        except Exception as exc:
            print(f"  ! {r.code_type} {r.code}: enrichment failed — {exc}")
            continue
        r.plain_english = enr.plain_english
        r.how_to_fix = enr.how_to_fix
        r.enrichment_source = "llm"
        r.last_enriched_at = datetime.utcnow()
        db.commit()
        enriched += 1
        print(f"  + {r.code_type} {r.code}")
        # Gentle pacing so we don't run up against any rate limits.
        time.sleep(0.25)
    skipped = db.query(AdjustmentCodeReference).filter(
        AdjustmentCodeReference.plain_english.isnot(None)
    ).count() - (enriched if reenrich else 0)
    return enriched, max(0, skipped)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-enrich", action="store_true", help="Skip LLM enrichment")
    ap.add_argument("--reenrich", action="store_true", help="Re-run LLM on every row")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        ins, upd = upsert_codes(db)
        total = db.query(AdjustmentCodeReference).count()
        print(f"Seed: {ins} inserted, {upd} verbiage updates, {total} rows total.")

        if args.no_enrich:
            return
        print("Enriching codes via Claude...")
        enriched, skipped = enrich(db, reenrich=args.reenrich)
        print(f"Enrichment: {enriched} done, {skipped} skipped (already enriched).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
