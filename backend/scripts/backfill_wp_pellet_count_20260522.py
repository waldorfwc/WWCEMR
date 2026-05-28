"""Backfill: May 22 2026 EOD physical pellet count for White Plains.

One-shot. Idempotent guard: re-runs are no-ops if the count row already
exists (same location/started_at/started_by).

What it does:
  1. Updates each listed lot's `expiration_date` from the placeholder
     2099-12-31 to the real Qualgen date (writes one
     `lot_expiration_corrected` audit row per change).
  2. Creates a PelletCount row for white_plains, started_at = 2026-05-22
     23:59 EDT, status=in_progress. ocooke@waldorfwomenscare.com is the
     starter; witness/finish happen in the UI.
  3. Inserts one PelletCountLine per lot with expected = current
     PelletStock.doses_on_hand and counted = the user-supplied number.
     Variance lines get a `notes` value so the finish endpoint accepts
     them (it 409s on any variance with empty notes).

Run from backend/ with venv active. Needs DATABASE_URL in env.
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

LOCATION = "white_plains"
ACTOR    = "ocooke@waldorfwomenscare.com"
# 2026-05-22 23:59:59 America/New_York (EDT, UTC-4) → 2026-05-23 03:59:59 UTC
COUNT_AT_UTC = dt.datetime(2026, 5, 23, 3, 59, 59)

# (qualgen_lot, real_expiration_iso, counted_doses_on_may22)
LOTS = [
    ("K290", "2026-09-23", 17),
    ("L058", "2027-02-16", 12),
    ("K225", "2026-07-21",  1),
    ("L001", "2026-11-20", 59),
    ("K227", "2026-07-21",  1),
    ("L011", "2026-11-20", 55),
    ("L015", "2026-11-20",  2),
    ("K213", "2026-07-21",  1),
    ("L009", "2026-11-20", 30),
    ("K301", "2026-10-25", 12),
    ("L012", "2026-12-12", 30),
    ("K304", "2026-10-25",  1),
    ("L006", "2026-12-12",  1),
    ("L070", "2027-02-17", 26),
    ("L003", "2026-12-12", 17),
    ("K263", "2026-09-23",  6),
    ("K330", "2026-12-01", 60),
    ("K354", "2026-12-12", 29),
    ("L018", "2027-01-05",  1),
    ("L034", "2027-01-19",  4),
]

COUNT_NOTES = (
    "Backfilled May 22 2026 EOD physical count for White Plains. "
    "Data entered May 27 by ocooke@waldorfwomenscare.com via "
    "scripts/backfill_wp_pellet_count_20260522.py. "
    "Witness signature pending — finish via UI."
)

VARIANCE_NOTE = (
    "Backfilled count: physical on-hand on 5/22 EOD differs from system "
    "expected; reconciliation per ocooke."
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

    # Verify every lot exists
    lot_rows = sess.execute(
        select(PelletLot).where(
            PelletLot.qualgen_lot_number.in_([n for n, _, _ in LOTS])
        )
    ).scalars().all()
    by_lot = {l.qualgen_lot_number: l for l in lot_rows}
    missing = [n for n, _, _ in LOTS if n not in by_lot]
    if missing:
        print(f"ERR: missing lots: {missing}", file=sys.stderr)
        sys.exit(3)

    # 1. Expiration corrections
    print("== Expiration corrections ==")
    exp_changes = 0
    for lot_no, exp_iso, _ in LOTS:
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

    # 2. The count row
    count = PelletCount(
        location=LOCATION,
        started_at=COUNT_AT_UTC,
        started_by=ACTOR,
        scope="all",
        status="in_progress",
        notes=COUNT_NOTES,
    )
    sess.add(count)
    sess.flush()  # need count.id for lines

    # 3. Lines, with notes pre-filled on variances so finish can succeed
    print()
    print("== Count lines ==")
    print(f"{'lot':5s}  {'expected':>8s}  {'counted':>7s}  {'Δ':>4s}")
    total_delta = 0
    for lot_no, _, counted in LOTS:
        lot = by_lot[lot_no]
        stock = sess.execute(
            select(PelletStock).where(
                PelletStock.lot_id   == lot.id,
                PelletStock.location == LOCATION,
            )
        ).scalars().first()
        expected = stock.doses_on_hand if stock else 0
        delta = counted - expected
        total_delta += delta
        line_notes = VARIANCE_NOTE if delta != 0 else None
        sess.add(PelletCountLine(
            count_id=count.id,
            lot_id=lot.id,
            expected_doses=expected,
            counted_doses=counted,
            counted_at=COUNT_AT_UTC,
            counted_by=ACTOR,
            notes=line_notes,
        ))
        print(f"  {lot_no:5s}  {expected:8d}  {counted:7d}  {delta:+4d}")

    sess.add(PelletAuditEvent(
        at=COUNT_AT_UTC, actor=ACTOR,
        action="count_started",
        count_id=count.id, location=LOCATION,
        summary=f"Backdated count started for {LOCATION} (May 22 2026 EOD)",
        detail={"lot_count": len(LOTS), "backfill": True,
                "net_delta": total_delta},
    ))

    sess.commit()
    print()
    print(f"PelletCount  : {count.id}")
    print(f"Location     : {LOCATION}")
    print(f"Started at   : {COUNT_AT_UTC} UTC  (2026-05-22 23:59 EDT)")
    print(f"Status       : in_progress  (finish + witness via UI)")
    print(f"Lines        : {len(LOTS)}")
    print(f"Exp corrects : {exp_changes}")
    print(f"Net delta    : {total_delta:+d} doses (system was over by this much)")


if __name__ == "__main__":
    main()
