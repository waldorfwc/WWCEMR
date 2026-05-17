"""One-time retro-migration: add the post_op_appts_scheduled milestone to
every active surgery that already had its milestones spawned before that
kind existed. Idempotent — safe to run multiple times.

Logic:
  - Skip surgeries in terminal states (cancelled / completed).
  - Skip surgeries with no milestones at all (still in 'incomplete' state).
  - Skip surgeries that already have a post_op_appts_scheduled row.
  - Otherwise, insert the milestone right after patient_picks_date,
    shifting all subsequent positions up by 1.
  - Mark the new row 'done' if the surgery already has all required
    post-op appt dates per the practice rules; otherwise 'pending'.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

from app.database import SessionLocal, init_db
from app.models.surgery import Surgery, SurgeryMilestone
from app.services.post_op_schedule import all_required_appts_filled


NEW_KIND = "post_op_appts_scheduled"
NEW_TITLE = "Post-op appointments scheduled"
NEW_EXPECTED_DAYS = 7


def main():
    init_db()
    db = SessionLocal()
    try:
        eligible = (
            db.query(Surgery)
              .filter(Surgery.status.in_(["new", "in_progress", "confirmed", "hold"]))
              .all()
        )
        n_inserted = 0
        n_already_present = 0
        n_skipped_no_ms = 0
        for s in eligible:
            ms = list(s.milestones or [])
            if not ms:
                n_skipped_no_ms += 1
                continue
            if any(m.kind == NEW_KIND for m in ms):
                n_already_present += 1
                continue

            # Find position of patient_picks_date so we can insert right after.
            anchor = next((m for m in ms if m.kind == "patient_picks_date"), None)
            insert_after = anchor.position if anchor else 0

            # Bump all milestones whose position > insert_after by 1
            for m in ms:
                if m.position > insert_after:
                    m.position += 1

            initial_status = "done" if all_required_appts_filled(s) else "pending"
            now = datetime.utcnow()
            new_m = SurgeryMilestone(
                surgery_id=s.id,
                kind=NEW_KIND,
                title=NEW_TITLE,
                position=insert_after + 1,
                status=initial_status,
                expected_duration_days=NEW_EXPECTED_DAYS,
                completed_at=now if initial_status == "done" else None,
                completed_by="system:retro-migration" if initial_status == "done" else None,
            )
            db.add(new_m)
            n_inserted += 1

        db.commit()
        print("Done.")
        print(f"  Active surgeries scanned: {len(eligible)}")
        print(f"  Milestone inserted:       {n_inserted}")
        print(f"  Already had it:           {n_already_present}")
        print(f"  Skipped (no milestones):  {n_skipped_no_ms}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
