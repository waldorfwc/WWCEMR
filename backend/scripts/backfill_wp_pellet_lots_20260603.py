"""Backfill three lots into White Plains pellet inventory.

Per Oliver: hand-add the following lots received outside the normal
Qualgen-order/receipt flow:

  - Estradiol 6mg   LOT K224  EXP 2026-07-21   1 dose
  - Estradiol 15mg  LOT K213  EXP 2026-07-21   1 dose
  - Testosterone 100mg LOT K255  EXP 2026-08-20  1 dose

Inserts:
  - One PelletLot per row (no receipt_id; this is a manual backfill).
  - One PelletStock row at white_plains with doses_on_hand = 1.
  - PelletAuditEvent 'lot_received' per lot.

Idempotent: skips any (dose_type, qualgen_lot_number) that already exists.

Run from backend/ with venv active and DATABASE_URL set:

    DATABASE_URL=postgresql://postgres:<pw>@127.0.0.1:5433/wwc_app \
        python -m scripts.backfill_wp_pellet_lots_20260603
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

# Make `app` importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.pellet import (
    PelletLot, PelletStock, PelletDoseType, PelletAuditEvent,
)


ACTOR    = "ocooke@waldorfwomenscare.com"
LOCATION = "white_plains"

# (hormone, dose_mg, qualgen_lot_number, expiration_date, doses_received)
LOTS = [
    ("estradiol",    6.0,   "K224", date(2026, 7, 21), 1),
    ("estradiol",   15.0,   "K213", date(2026, 7, 21), 1),
    ("testosterone", 100.0, "K255", date(2026, 8, 20), 1),
]


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    sess = Session()
    now = datetime.utcnow()

    created = 0
    skipped = 0
    try:
        for hormone, dose_mg, lot_no, exp, doses in LOTS:
            dt = sess.execute(
                select(PelletDoseType).where(
                    PelletDoseType.hormone == hormone,
                    PelletDoseType.dose_mg == Decimal(str(dose_mg)),
                )
            ).scalars().first()
            if dt is None:
                print(f"  SKIP {hormone} {dose_mg}mg lot {lot_no}: dose type not found")
                skipped += 1
                continue

            # Idempotency: skip if a lot with this dose+number already exists
            existing = sess.execute(
                select(PelletLot).where(
                    PelletLot.dose_type_id == dt.id,
                    PelletLot.qualgen_lot_number == lot_no,
                )
            ).scalars().first()
            if existing is not None:
                print(f"  SKIP {hormone} {dose_mg}mg lot {lot_no}: already exists "
                       f"(id={existing.id})")
                skipped += 1
                continue

            lot = PelletLot(
                dose_type_id=dt.id,
                qualgen_lot_number=lot_no,
                expiration_date=exp,
                doses_originally_received=doses,
                packs_received=None,
                pack_size=None,
                receipt_id=None,
                received_at=now,
                received_by=ACTOR,
                notes="Manual backfill 2026-06-03 — added outside Qualgen flow",
            )
            sess.add(lot)
            sess.flush()

            stock = PelletStock(
                lot_id=lot.id,
                location=LOCATION,
                doses_on_hand=doses,
                status="active",
            )
            sess.add(stock)

            sess.add(PelletAuditEvent(
                actor=ACTOR,
                action="lot_received",
                lot_id=lot.id,
                dose_type_id=dt.id,
                location=LOCATION,
                summary=(f"Manually added {doses} dose {dt.label} lot {lot_no} "
                          f"exp {exp.isoformat()} to {LOCATION} (backfill)"),
            ))

            print(f"  CREATE {hormone} {dose_mg}mg lot {lot_no} → "
                   f"lot_id={lot.id}, doses_on_hand={doses} at {LOCATION}")
            created += 1

        sess.commit()
    finally:
        sess.close()

    print(f"\nDone. created={created} skipped={skipped}")


if __name__ == "__main__":
    main()
