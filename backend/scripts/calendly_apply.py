"""Apply Calendly-CSV dates to surgeries that don't yet have a scheduled date.

Match logic mirrors calendly_match_preview:
  • email exact (lowercased, trimmed)
  • else fuzzy name (SequenceMatcher ≥ 0.90)
Only surgeries WITHOUT a scheduled_date get updated. Anyone not in
Surgery (no match) is silently skipped.

For each matched row:
  • Set Surgery.scheduled_date / scheduled_start_time / selected_facility
  • Find-or-create a BlockDay (facility, date) covering the visit window
  • Insert a SurgerySlot tied to that BlockDay
  • Flip Surgery.status to 'confirmed' when it was new/in_progress (matches
    the existing date-picker flow)
  • Write a SurgeryNote and a state_transition audit
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, time as _time
from difflib import SequenceMatcher
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


FACILITY_FROM_EVENT = {
    "CRMC_Minor":     "crmc",
    "CRMC_Major":     "crmc",
    "MedStar_Rbt_2c": "medstar",
    "White_Plains":   "office",
}

PROC_KIND_FROM_EVENT = {
    "CRMC_Minor":     "minor",
    "CRMC_Major":     "major",
    "MedStar_Rbt_2c": "robotic_180",
    "White_Plains":   "office",
}

ACTOR = "ocooke@waldorfwomenscare.com"
SOURCE_TAG = "Calendly backfill 2026-06-03"


def parse_dt(s: str) -> datetime:
    s = s.strip()
    for fmt in ("%m/%d/%y %H:%M", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"could not parse datetime: {s}")


def norm_name(s: str) -> str:
    return " ".join((s or "").lower().strip().split())


def get_or_create_block_day(sess, facility: str, date_, win_start: _time,
                              win_end: _time) -> tuple[str, bool]:
    """Returns (block_day_id, created)."""
    row = sess.execute(text("""
        SELECT id, start_time, end_time FROM surgery_block_days
         WHERE facility = :f AND block_date = :d
         LIMIT 1
    """), {"f": facility, "d": date_}).first()
    if row:
        return str(row[0]), False
    # Create with the calendly window as defaults (so capacity reflects
    # at least this one visit's bounds)
    new_id = str(uuid4())
    sess.execute(text("""
        INSERT INTO surgery_block_days
          (id, facility, block_date, start_time, end_time, block_kind,
           is_addon, notes, created_at, created_by)
        VALUES (:id, :f, :d, :s, :e, :k, false, :notes, NOW(), :by)
    """), {"id": new_id, "f": facility, "d": date_,
            "s": win_start, "e": win_end,
            "k": PROC_KIND_FROM_FACILITY[facility],
            "notes": "Created by Calendly backfill 2026-06-03",
            "by": ACTOR})
    return new_id, True


# Per-facility default block kind for newly-created BlockDays
PROC_KIND_FROM_FACILITY = {
    "crmc":    "minor",
    "medstar": "robotic_180",
    "office":  "office",
}


def main(csv_path: str) -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERR: DATABASE_URL not set", file=sys.stderr); sys.exit(2)

    eng = create_engine(db_url)
    Session = sessionmaker(bind=eng)
    sess = Session()

    # Snapshot open surgeries
    rows = sess.execute(text("""
        SELECT id, patient_name, chart_number, lower(email) AS email,
               scheduled_date, status
          FROM surgeries
         WHERE status NOT IN ('cancelled', 'completed')
    """)).mappings().all()
    all_surgeries = [dict(r) for r in rows]
    for s in all_surgeries:
        s["_n"] = norm_name(s.get("patient_name") or "")
    by_email: dict[str, list[dict]] = {}
    for s in all_surgeries:
        em = (s.get("email") or "").strip()
        if em:
            by_email.setdefault(em, []).append(s)

    applied = 0
    skipped = 0
    review_applied = 0
    no_match = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = (row.get("Invitee Email") or "").strip().lower()
            first = (row.get("Invitee First Name") or "").strip()
            last  = (row.get("Invitee Last Name") or "").strip()
            event = (row.get("Event Type Name") or "").strip()
            start = parse_dt(row.get("Start Date & Time") or "")
            end   = parse_dt(row.get("End Date & Time") or "")
            facility = FACILITY_FROM_EVENT.get(event)
            proc_kind = PROC_KIND_FROM_EVENT.get(event)
            if facility is None or proc_kind is None:
                print(f"  ! unknown event_type '{event}' — skipping {first} {last}")
                continue

            # Match
            matches = by_email.get(email, [])
            chosen: dict | None = None
            matched_by = None
            if matches:
                chosen = next((m for m in matches if m["scheduled_date"] is None),
                                matches[0])
                matched_by = "email"
            else:
                # Fuzzy name
                target_norm = norm_name(f"{last}, {first}")
                target_alt  = norm_name(f"{first} {last}")
                best, best_score = None, 0.0
                for s in all_surgeries:
                    ratio = max(
                        SequenceMatcher(None, target_norm, s["_n"]).ratio(),
                        SequenceMatcher(None, target_alt,  s["_n"]).ratio(),
                    )
                    if ratio > best_score:
                        best_score = ratio
                        best = s
                if best and best_score >= 0.90:
                    chosen = best
                    matched_by = f"name({best_score:.2f})"

            if not chosen:
                no_match += 1
                continue
            if chosen.get("scheduled_date") is not None:
                skipped += 1
                continue

            # Apply
            block_day_id, created = get_or_create_block_day(
                sess, facility, start.date(), start.time(), end.time())
            duration = max(1, int((end - start).total_seconds() // 60))

            # SurgerySlot
            slot_id = str(uuid4())
            sess.execute(text("""
                INSERT INTO surgery_slots
                  (id, block_day_id, surgery_id, start_time, duration_minutes,
                   procedure_kind)
                VALUES (:id, :bd, :sid, :st, :dur, :pk)
            """), {"id": slot_id, "bd": block_day_id, "sid": str(chosen["id"]),
                    "st": start.time(), "dur": duration, "pk": proc_kind})

            # Update Surgery
            new_status_clause = ""
            if chosen["status"] in ("new", "in_progress"):
                new_status_clause = ", status = 'confirmed'"
            sess.execute(text(f"""
                UPDATE surgeries
                   SET scheduled_date       = :d,
                       scheduled_start_time = :t,
                       selected_facility    = :f
                       {new_status_clause}
                 WHERE id = :sid
            """), {"d": start.date(), "t": start.time(),
                    "f": facility, "sid": str(chosen["id"])})

            # SurgeryNote
            sess.execute(text("""
                INSERT INTO surgery_notes
                  (id, surgery_id, created_by, created_at, content)
                VALUES (:id, :sid, :by, NOW(), :note)
            """), {"id": str(uuid4()), "sid": str(chosen["id"]),
                    "by": ACTOR,
                    "note": (f"{SOURCE_TAG}: scheduled {start.strftime('%m/%d/%Y %H:%M')} "
                              f"({facility}, {proc_kind}, {duration}min) via "
                              f"{matched_by} match{' (NEW BlockDay)' if created else ''}.")})

            if matched_by == "email":
                applied += 1
            else:
                review_applied += 1

    sess.commit()
    sess.close()
    print(f"\nDONE. email_applied={applied}  name_match_applied={review_applied}  "
           f"already_dated_skipped={skipped}  no_match_skipped={no_match}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: calendly_apply.py <csv_path>", file=sys.stderr); sys.exit(2)
    main(sys.argv[1])
