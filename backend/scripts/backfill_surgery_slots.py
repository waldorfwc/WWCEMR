"""One-off: create SurgerySlot rows for any Surgery that has a
scheduled_date + scheduled_start_time but is missing its slot record.

Caused by the Calendly / ModMed importers populating the date fields on
the Surgery row directly without going through the slot-booking flow.
The dashboard's per-block-day case counts read from SurgerySlot, so
without these rows the day shows '0 cases booked' even though surgeries
are scheduled.

Idempotent — surgeries that already have a slot are skipped.
"""
from __future__ import annotations

import os
import sys
from datetime import date as _date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal, init_db
from app.models.surgery import Surgery, SurgerySlot, BlockDay
from app.services.surgery_block_schedule import DURATIONS


def main():
    init_db()
    db = SessionLocal()
    try:
        # All surgeries with a date but no slot row, status not cancelled
        rows = (db.query(Surgery)
                  .filter(Surgery.scheduled_date.isnot(None),
                          Surgery.scheduled_start_time.isnot(None),
                          Surgery.selected_facility.isnot(None),
                          Surgery.status.notin_(["cancelled"]))
                  .all())
        n_created = 0
        n_already = 0
        n_no_block = 0
        n_skipped = 0

        for s in rows:
            existing = db.query(SurgerySlot).filter(SurgerySlot.surgery_id == s.id).first()
            if existing:
                n_already += 1
                continue
            bd = (db.query(BlockDay)
                    .filter(BlockDay.block_date == s.scheduled_date,
                            BlockDay.facility == s.selected_facility)
                    .first())
            if not bd:
                n_no_block += 1
                print(f"  no block day for {s.patient_name} on {s.scheduled_date} at {s.selected_facility}")
                continue
            proc_kind = s.procedure_classification or "minor"
            duration = DURATIONS.get(proc_kind, s.estimated_minutes or 60)
            slot = SurgerySlot(
                surgery_id=s.id,
                block_day_id=bd.id,
                start_time=s.scheduled_start_time,
                duration_minutes=duration,
                procedure_kind=proc_kind,
            )
            db.add(slot)
            n_created += 1

        db.commit()
        print()
        print(f"  Surgeries scanned:        {len(rows)}")
        print(f"  Slots created:            {n_created}")
        print(f"  Already had slot:         {n_already}")
        print(f"  No matching block-day:    {n_no_block}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
