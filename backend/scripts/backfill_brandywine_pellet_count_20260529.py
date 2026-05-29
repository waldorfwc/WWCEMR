"""Backfill: 2026-05-29 morning physical pellet count for Brandywine.

User's count (Oliver, 2026-05-29 after bagging today's 2 doses) lists 12
lots that are the *complete* current Brandywine inventory. The DB has 28
lots with non-zero brandywine stock; the other 27 are stale ghost stock
that needs to be zeroed.

What this script does (single transaction):
  1. Corrects expiration_date on the 12 listed lots from the 2099-12-31
     placeholder to the real Qualgen date. Writes one
     `lot_expiration_corrected` audit row per change.
  2. Pre-creates PelletStock rows at brandywine for any of the 12 listed
     lots that don't have one. They start at doses_on_hand=0 so the
     finish_count handler picks them up via the strict (lot, location)
     match instead of falling back to a stock row in another location.
  3. Creates the PelletCount row (brandywine, started_at = today 09:00
     EDT, scope='all', status='in_progress').
  4. Inserts 39 PelletCountLines:
       - 12 lines for listed lots (expected = current on-hand, counted =
         Oliver's number)
       - 27 lines for ghost lots (expected = current on-hand, counted = 0)
     Variance lines get a notes value so finish_count won't 409.

User finishes/witnesses the count in the UI; that's when stock actually
moves and stock_adjusted audit rows are written.

Idempotency guard: re-runs are no-ops if a PelletCount already exists for
brandywine with the same started_at/started_by.
"""

import os
import sys
import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.models.pellet import (
    PelletLot, PelletStock, PelletCount, PelletCountLine, PelletAuditEvent
)

LOCATION = "brandywine"
ACTOR    = "ocooke@waldorfwomenscare.com"
# 2026-05-29 09:00 America/New_York (EDT, UTC-4) → 2026-05-29 13:00 UTC
COUNT_AT_UTC = dt.datetime(2026, 5, 29, 13, 0, 0)

# (qualgen_lot, real_expiration_iso, counted_doses) — Oliver's count
LISTED_LOTS = [
    ("K349", "2026-11-20", 20),
    ("K296", "2026-08-11", 30),
    ("K187", "2026-06-02",  1),
    ("L015", "2026-11-20",  8),
    ("L009", "2026-11-20", 18),
    ("L012", "2026-12-12", 30),
    ("K252", "2026-08-20",  9),
    ("L070", "2027-02-17",  9),
    ("K257", "2026-08-20", 15),
    ("K297", "2026-10-25", 26),
    ("L034", "2027-01-19",  8),
    ("L016", "2026-12-12", 30),
]

COUNT_NOTES = (
    "Physical count of Brandywine performed 2026-05-29 (after bagging "
    "today's 2 doses) by Oliver. The 12 listed lots are the complete "
    "current inventory; 27 ghost lots with stale system stock are being "
    "zeroed as part of this reconciliation. "
    "Witness signature pending — finish via UI."
)

LISTED_VARIANCE_NOTE = (
    "Reconciled to physical count on 2026-05-29 (Oliver)."
)

GHOST_NOTE = (
    "Lot is no longer present at Brandywine on 2026-05-29; "
    "zero-out reconciliation."
)


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    sess = Session()

    # Idempotency
    existing = sess.execute(
        select(PelletCount).where(
            PelletCount.location   == LOCATION,
            PelletCount.started_at == COUNT_AT_UTC,
            PelletCount.started_by == ACTOR,
        )
    ).scalars().first()
    if existing:
        print(f"Refusing: PelletCount {existing.id} already exists. Nothing to do.")
        return

    # Verify every listed lot exists
    listed_nos = [n for n, _, _ in LISTED_LOTS]
    lot_rows = sess.execute(
        select(PelletLot).where(PelletLot.qualgen_lot_number.in_(listed_nos))
    ).scalars().all()
    by_lot = {l.qualgen_lot_number: l for l in lot_rows}
    missing = [n for n in listed_nos if n not in by_lot]
    if missing:
        print(f"ERR: missing lots: {missing}", file=sys.stderr)
        sys.exit(3)

    # 1. Expiration corrections (listed lots only)
    print("== Expiration corrections ==")
    exp_changes = 0
    for lot_no, exp_iso, _ in LISTED_LOTS:
        lot = by_lot[lot_no]
        new_exp = dt.date.fromisoformat(exp_iso)
        if lot.expiration_date != new_exp:
            old = lot.expiration_date
            lot.expiration_date = new_exp
            sess.add(PelletAuditEvent(
                at=COUNT_AT_UTC, actor=ACTOR,
                action="lot_expiration_corrected",
                dose_type_id=lot.dose_type_id, lot_id=lot.id,
                location=LOCATION,
                summary=f"{lot_no} expiration {old} -> {new_exp}",
                detail={"from": str(old), "to": str(new_exp),
                        "reason": "backfill placeholder to real Qualgen date"},
            ))
            exp_changes += 1
            print(f"  {lot_no:5s}  {old} -> {new_exp}")
    if exp_changes == 0:
        print("  (none)")

    # 2. Pre-create PelletStock(lot, brandywine, 0) for listed lots without one
    print()
    print("== Pre-creating missing brandywine stock rows ==")
    stock_creates = 0
    for lot_no, _, _ in LISTED_LOTS:
        lot = by_lot[lot_no]
        s = sess.execute(
            select(PelletStock).where(
                PelletStock.lot_id   == lot.id,
                PelletStock.location == LOCATION,
            )
        ).scalars().first()
        if s is None:
            sess.add(PelletStock(lot_id=lot.id, location=LOCATION,
                                  doses_on_hand=0))
            stock_creates += 1
            print(f"  + PelletStock(lot={lot_no}, {LOCATION}, 0)")
    sess.flush()
    if stock_creates == 0:
        print("  (none — every listed lot already had a brandywine row)")

    # 3. The count row
    count = PelletCount(
        location=LOCATION,
        started_at=COUNT_AT_UTC,
        started_by=ACTOR,
        scope="all",
        status="in_progress",
        notes=COUNT_NOTES,
    )
    sess.add(count)
    sess.flush()

    # 4a. Lines for the 12 listed lots
    print()
    print("== Listed lots (12) ==")
    print(f"  {'lot':5s}  {'expected':>8s}  {'counted':>7s}  {'Δ':>5s}")
    listed_delta = 0
    for lot_no, _, counted in LISTED_LOTS:
        lot = by_lot[lot_no]
        s = sess.execute(
            select(PelletStock).where(
                PelletStock.lot_id   == lot.id,
                PelletStock.location == LOCATION,
            )
        ).scalars().first()
        expected = s.doses_on_hand if s else 0
        delta = counted - expected
        listed_delta += delta
        sess.add(PelletCountLine(
            count_id=count.id, lot_id=lot.id,
            expected_doses=expected, counted_doses=counted,
            counted_at=COUNT_AT_UTC, counted_by=ACTOR,
            notes=(LISTED_VARIANCE_NOTE if delta != 0 else None),
        ))
        print(f"  {lot_no:5s}  {expected:8d}  {counted:7d}  {delta:+5d}")

    # 4b. Lines for ghost lots — every brandywine stock row with on_hand > 0
    # that isn't in the listed set. Set counted=0.
    print()
    print("== Ghost lots being zeroed ==")
    listed_lot_ids = {by_lot[n].id for n in listed_nos}
    ghost_rows = sess.execute(
        select(PelletStock, PelletLot)
        .join(PelletLot, PelletLot.id == PelletStock.lot_id)
        .where(PelletStock.location == LOCATION,
               PelletStock.doses_on_hand > 0,
               PelletStock.lot_id.notin_(listed_lot_ids))
        .order_by(PelletLot.qualgen_lot_number)
    ).all()
    ghost_delta = 0
    for s, lot in ghost_rows:
        expected = s.doses_on_hand
        delta = 0 - expected
        ghost_delta += delta
        sess.add(PelletCountLine(
            count_id=count.id, lot_id=lot.id,
            expected_doses=expected, counted_doses=0,
            counted_at=COUNT_AT_UTC, counted_by=ACTOR,
            notes=GHOST_NOTE,
        ))
        print(f"  {lot.qualgen_lot_number:5s}  {expected:8d}  {0:7d}  {delta:+5d}")

    sess.add(PelletAuditEvent(
        at=COUNT_AT_UTC, actor=ACTOR,
        action="count_started",
        count_id=count.id, location=LOCATION,
        summary=f"Count started for {LOCATION} (2026-05-29 morning)",
        detail={"lot_count": len(LISTED_LOTS) + len(ghost_rows),
                "listed_lots": len(LISTED_LOTS),
                "ghost_lots":  len(ghost_rows),
                "net_delta":   listed_delta + ghost_delta},
    ))

    sess.commit()

    print()
    print(f"PelletCount   : {count.id}")
    print(f"Location      : {LOCATION}")
    print(f"Started at    : {COUNT_AT_UTC} UTC  (2026-05-29 09:00 EDT)")
    print(f"Status        : in_progress  (finish + witness via UI)")
    print(f"Listed lines  : {len(LISTED_LOTS)}    delta = {listed_delta:+d}")
    print(f"Ghost lines   : {len(ghost_rows)}    delta = {ghost_delta:+d}")
    print(f"Exp corrects  : {exp_changes}")
    print(f"Stock creates : {stock_creates}")
    print(f"Net delta     : {listed_delta + ghost_delta:+d} doses on this location")


if __name__ == "__main__":
    main()
