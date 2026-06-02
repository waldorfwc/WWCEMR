"""Backfill helper: any device currently classified as 'wwc_claimed'
whose active assigned patient is NOT already on the Owed list gets
pushed there. Used to repair the gap from before the change-ownership
endpoint started auto-creating Owed rows.

Run as:
  python -m scripts.larc_backfill_owed_from_claimed             # dry-run
  python -m scripts.larc_backfill_owed_from_claimed --apply     # commits
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.main  # noqa: F401 — registers all SQLAlchemy mappers

from app.database import SessionLocal
from app.models.larc import (
    LarcAssignment, LarcDevice, LarcOwedPatient,
)
from app.services.larc_sweeps import _push_to_owed


def main(apply: bool, only_our_id: str | None = None) -> None:
    db = SessionLocal()
    actor = "system:owed_backfill_2026-06-02"

    q = db.query(LarcDevice).filter(LarcDevice.ownership == "wwc_claimed")
    if only_our_id:
        q = q.filter(LarcDevice.our_id == only_our_id)
    devices = q.all()

    print(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")
    print(f"WWC-claimed devices to check: {len(devices)}")
    if only_our_id:
        print(f"   (filtered to our_id={only_our_id})")

    added: list[str] = []
    skipped: list[str] = []
    for d in devices:
        active = next((a for a in (d.assignments or [])
                       if a.is_active and a.chart_number), None)
        if not active:
            skipped.append(f"{d.our_id}: no active assignment with chart")
            continue
        existing = (db.query(LarcOwedPatient)
                      .filter(LarcOwedPatient.chart_number == active.chart_number,
                              LarcOwedPatient.original_assignment_id == active.id,
                              LarcOwedPatient.resolved_at.is_(None))
                      .first())
        if existing:
            skipped.append(f"{d.our_id}: chart {active.chart_number} already on Owed list")
            continue
        added.append(f"{d.our_id}: {active.patient_name} (chart {active.chart_number})")
        if apply:
            _push_to_owed(
                db, active,
                expires_at=d.expiration_date,
                actor=actor,
                summary=("Added to Owed list: device was claimed by WWC "
                         "(backfill 2026-06-02)."),
            )

    print(f"\nWould add {len(added)} patients to Owed list:")
    for line in added[:20]:
        print(f"   + {line}")
    if len(added) > 20:
        print(f"   ... and {len(added) - 20} more")
    print(f"\nSkipped {len(skipped)}:")
    for line in skipped[:10]:
        print(f"   - {line}")
    if len(skipped) > 10:
        print(f"   ... and {len(skipped) - 10} more")

    if apply:
        db.commit()
        print("\n✓ Committed.")
    else:
        print("\nDRY-RUN — no changes committed. Re-run with --apply.")
    db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--only", default=None, help="Restrict to one our_id (e.g. WWC0043)")
    args = p.parse_args()
    main(apply=args.apply, only_our_id=args.only)
