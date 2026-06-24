"""One-time: merge duplicate pellet lots (same qualgen_lot_number + dose_type +
office) into a single canonical record. Backfills PelletLot.location first.

Idempotent — re-running after a clean merge is a no-op. Run with --dry-run to
print the plan without writing (the default); pass --apply to commit changes.

Canonical per group: prefer a receipt-backed lot with a real (non-placeholder)
expiration; tie-break by earliest received_at.

Safety: asserts total stock doses and total doses_originally_received are
unchanged across the run; skips (and reports) any lot whose stock/doses span
more than one office.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.orm import Session

from app.database import SessionLocal, init_db
from app.models.pellet import (
    PelletLot, PelletReceipt, PelletStock, PelletVisitDose,
)
from app.services.pellet.lot_merge import merge_lot, UNKNOWN_EXP


def _lot_offices(db: Session, lot: PelletLot) -> set:
    """Distinct offices a lot touches: its stock rows + its doses' visit
    locations. Used to detect a lot spanning >1 office (unsafe to auto-merge)."""
    offices = set(
        loc for (loc,) in db.query(PelletStock.location)
                            .filter(PelletStock.lot_id == lot.id).all())
    rows = (db.query(PelletVisitDose)
              .filter(PelletVisitDose.lot_id == lot.id).all())
    for d in rows:
        # d.visit is a real relationship on PelletVisitDose
        v = d.visit
        if v is not None and v.location:
            offices.add(v.location)
    return offices


def backfill_lot_locations(db: Session) -> int:
    """Set location on every lot that has none: its single stock-row location,
    else its receipt's location, else the modal location of its doses' visits."""
    n = 0
    for lot in db.query(PelletLot).filter(PelletLot.location.is_(None)).all():
        loc = None
        srows = db.query(PelletStock).filter(PelletStock.lot_id == lot.id).all()
        locs = {s.location for s in srows}
        if len(locs) == 1:
            loc = next(iter(locs))
        elif lot.receipt_id is not None:
            # PelletLot has no .receipt relationship; query explicitly.
            receipt = db.query(PelletReceipt).filter(
                PelletReceipt.id == lot.receipt_id).first()
            if receipt is not None:
                loc = receipt.location
        else:
            doses = db.query(PelletVisitDose).filter(
                PelletVisitDose.lot_id == lot.id).all()
            counts: dict = defaultdict(int)
            for d in doses:
                if d.visit is not None and d.visit.location:
                    counts[d.visit.location] += 1
            if counts:
                loc = max(counts, key=counts.get)
        if loc:
            lot.location = loc
            n += 1
    db.flush()
    return n


def dedup_lots(db: Session, *, actor: str = "system:lot-dedup",
               dry_run: bool = True) -> dict:
    backfill_lot_locations(db)

    stock_before = db.query(PelletStock).with_entities(
        PelletStock.doses_on_hand).all()
    total_stock_before = sum(s[0] for s in stock_before)
    total_orig_before = sum(
        (lot.doses_originally_received or 0) for lot in db.query(PelletLot).all())

    groups: dict = defaultdict(list)
    for lot in db.query(PelletLot).all():
        if lot.location is None:
            continue  # un-backfillable; left for manual review
        groups[(lot.qualgen_lot_number, str(lot.dose_type_id), lot.location)].append(lot)

    stats = {"groups_seen": 0, "groups_merged": 0, "lots_deleted": 0,
             "skipped_multi_office": [], "plan": []}

    for key, lots in groups.items():
        if len(lots) < 2:
            continue
        stats["groups_seen"] += 1
        # Single-office guard: every lot in the group must touch only this office.
        office = key[2]
        bad = [str(l.id) for l in lots if (_lot_offices(db, l) - {office})]
        if bad:
            stats["skipped_multi_office"].append({"key": key, "lots": bad})
            continue

        # Canonical: receipt-backed + real exp first; tie-break earliest received_at.
        def rank(l: PelletLot):
            is_receipt_real = (l.receipt_id is not None
                               and l.expiration_date != UNKNOWN_EXP)
            return (0 if is_receipt_real else 1, l.received_at or datetime.min)

        canonical = sorted(lots, key=rank)[0]
        dups = [l for l in lots if l.id != canonical.id]
        stats["plan"].append({"key": key, "canonical": str(canonical.id),
                              "merge": [str(d.id) for d in dups]})
        if not dry_run:
            for dup in dups:
                merge_lot(db, src=dup, dst=canonical, actor=actor)
                stats["lots_deleted"] += 1
            stats["groups_merged"] += 1

    if not dry_run:
        db.flush()
        total_stock_after = sum(
            s[0] for s in db.query(PelletStock).with_entities(
                PelletStock.doses_on_hand).all())
        total_orig_after = sum(
            (lot.doses_originally_received or 0) for lot in db.query(PelletLot).all())
        assert total_stock_after == total_stock_before, (
            f"stock total changed {total_stock_before} -> {total_stock_after}")
        assert total_orig_after == total_orig_before, (
            f"orig-received total changed {total_orig_before} -> {total_orig_after}")

    return stats


def main() -> None:
    dry = "--apply" not in sys.argv
    init_db()
    db: Session = SessionLocal()
    try:
        stats = dedup_lots(db, dry_run=dry)
        if dry:
            print("DRY RUN — no changes written")
        else:
            db.commit()
            print("APPLIED")
        print(f"  duplicate groups: {stats['groups_seen']}")
        print(f"  groups merged:    {stats['groups_merged']}")
        print(f"  lots deleted:     {stats['lots_deleted']}")
        if stats["skipped_multi_office"]:
            print(
                f"  SKIPPED (multi-office, manual review): "
                f"{stats['skipped_multi_office']}")
        for p in stats["plan"]:
            print(
                f"   {p['key']}: keep {p['canonical'][:8]}, "
                f"merge {[x[:8] for x in p['merge']]}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
