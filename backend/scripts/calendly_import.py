"""Calendly events export → surgery appt-time matcher.

Run mode is controlled by the --apply flag:
  default (dry-run): reports matches, ambiguities, misses. No DB writes.
  --apply         : applies time / facility updates to matched surgeries.
                    Unmatched rows are NOT auto-created (we ask first).

Match strategy per row:
  1. By Invitee Email (exact, case-insensitive) → match
  2. By Invitee Name (last, first OR first last, normalised) → match
  3. Multiple matches → AMBIGUOUS (report for human review)
  4. No match → UNMATCHED (report for human review)

Canceled rows are reported separately, never applied.
"""
import argparse
import csv
import os
import sys
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import or_

from app.database import SessionLocal, init_db
from app.models.surgery import Surgery


LOCATION_TO_FACILITY = {
    "medstar southern maryland hospital": "medstar",
    "um charles regional medical center": "crmc",
    # WWC office — Calendly puts the street address in the Location column
    "4470 regency pl, ste 106, white plains, md 20695": "office",
}


def parse_dt(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def norm_name(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _tiebreak_by_date(rows, start_dt):
    """When multiple surgery rows match the same patient, prefer the one
    whose scheduled_date already matches Calendly's date (likely the same
    appointment, just needs the time). Returns a single-row list if a clear
    winner exists, else the original list."""
    if not start_dt or not rows:
        return rows
    target = start_dt.date()
    same_date = [s for s in rows if s.scheduled_date == target]
    if len(same_date) == 1:
        return same_date
    # If no row matches the date but exactly one is undated, prefer that one
    undated = [s for s in rows if s.scheduled_date is None]
    if not same_date and len(undated) == 1:
        return undated
    return rows


def match_surgery(db, row, start_dt=None) -> tuple[str, list[Surgery]]:
    """Returns ('email' | 'name' | 'none' | 'ambiguous', list_of_matches)."""
    email = (row.get("Invitee Email") or "").strip().lower()
    first = (row.get("Invitee First Name") or "").strip()
    last = (row.get("Invitee Last Name") or "").strip()

    # Active surgeries only — match against ones still in flight
    active_filter = Surgery.status.in_(["new", "in_progress", "confirmed", "hold"])

    # 1) Match by email
    if email:
        rows = (db.query(Surgery)
                  .filter(active_filter, Surgery.email.ilike(email))
                  .all())
        rows = _tiebreak_by_date(rows, start_dt)
        if len(rows) == 1:
            return "email", rows
        if len(rows) > 1:
            return "ambiguous", rows

    # 2) Match by name — must match BOTH first and last
    if first and last:
        first_lc = first.lower()
        last_lc = last.lower()
        candidates = (db.query(Surgery)
                        .filter(active_filter,
                                Surgery.patient_name.ilike(f"%{last}%"))
                        .all())
        rows = [s for s in candidates
                if first_lc in (s.patient_name or "").lower()
                   and last_lc in (s.patient_name or "").lower()]
        rows = _tiebreak_by_date(rows, start_dt)
        if len(rows) == 1:
            return "name", rows
        if len(rows) > 1:
            return "ambiguous", rows

    return "none", []


def facility_from_location(loc: str) -> Optional[str]:
    return LOCATION_TO_FACILITY.get((loc or "").strip().lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="Path to Calendly export CSV")
    ap.add_argument("--apply", action="store_true",
                     help="Actually apply updates. Without it, just reports.")
    ap.add_argument("--no-create-stubs", action="store_true",
                     help="Skip stub-creation for unmatched rows (match-only mode).")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        with open(args.csv, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        matched_email = []
        matched_name = []
        ambiguous = []
        unmatched = []
        canceled = []
        no_facility = []

        updates_to_apply = []

        for r in rows:
            if (r.get("Canceled") or "").lower() == "true":
                canceled.append(r)
                continue

            start_dt = parse_dt(r.get("Start Date & Time"))
            kind, candidates = match_surgery(db, r, start_dt=start_dt)
            fac = facility_from_location(r.get("Location") or "")

            if not start_dt:
                continue

            entry = {
                "csv_row": r,
                "candidates": candidates,
                "match_kind": kind,
                "start_dt": start_dt,
                "facility": fac,
            }

            if kind == "email":
                matched_email.append(entry)
            elif kind == "name":
                matched_name.append(entry)
            elif kind == "ambiguous":
                ambiguous.append(entry)
            else:
                unmatched.append(entry)

            if not fac:
                no_facility.append(entry)

            if kind in ("email", "name"):
                updates_to_apply.append(entry)

        # ── REPORT ─────────────────────────────────────────────────
        print(f"\nCalendly rows: {len(rows)}")
        print(f"  Canceled (skipped):    {len(canceled)}")
        print(f"  Matched by email:      {len(matched_email)}")
        print(f"  Matched by name:       {len(matched_name)}")
        print(f"  AMBIGUOUS:             {len(ambiguous)}")
        print(f"  UNMATCHED:             {len(unmatched)}")
        print(f"  No facility mapping:   {len(no_facility)}")

        if ambiguous:
            print("\n── AMBIGUOUS (multiple surgeries matched) ──")
            for e in ambiguous:
                r = e["csv_row"]
                print(f"  Calendly: {r.get('Invitee Name')!r}  email={r.get('Invitee Email')!r}")
                for c in e["candidates"]:
                    print(f"    candidate id={c.id} name={c.patient_name!r} chart={c.chart_number} dob={c.dob}")

        if unmatched:
            print("\n── UNMATCHED (no surgery in DB) ──")
            for e in unmatched:
                r = e["csv_row"]
                print(f"  {r.get('Invitee Name')!r}  email={r.get('Invitee Email')!r}  "
                      f"event={r.get('Event Type Name')}  date={e['start_dt']}")

        if no_facility:
            print("\n── No facility mapping (need rule for this location) ──")
            for e in no_facility:
                loc = e["csv_row"].get("Location")
                print(f"  Location={loc!r}")

        if not args.apply:
            ambig_to_apply = len([e for e in ambiguous if e["candidates"]])
            new_stubs = len(unmatched)
            print(f"\nDRY RUN — no changes applied. Run with --apply to:")
            print(f"  Update                 {len(updates_to_apply)} matched surgeries")
            print(f"  Apply to ambiguous     {ambig_to_apply} (first candidate by created_at)")
            print(f"  Create stub surgeries  {new_stubs} (status=incomplete)")
            return

        # ── APPLY ──────────────────────────────────────────────────
        EVENT_TO_CLASSIFICATION = {
            "MedStar_Rbt_2c": "robotic_180",
            "CRMC_Minor":     "minor",
            "CRMC_Major":     "major",
            "White_Plains":   "office",
        }
        EVENT_DURATION = {
            "robotic_180": 180, "robotic_240": 240,
            "minor": 90, "major": 180, "office": 60,
        }

        def _apply_to(s: Surgery, e):
            start_dt = e["start_dt"]
            fac = e["facility"]
            s.scheduled_date = start_dt.date()
            s.scheduled_start_time = start_dt.time()
            if fac:
                s.selected_facility = fac
                el = list(s.eligible_facilities or [])
                if fac not in el:
                    el.append(fac)
                    s.eligible_facilities = el

        applied = 0
        for e in updates_to_apply:
            _apply_to(e["candidates"][0], e)
            applied += 1

        # Apply ambiguous by picking the OLDEST (first-created) candidate.
        # The other duplicates are left untouched for manual review.
        ambig_applied = 0
        for e in ambiguous:
            cands = sorted(e["candidates"], key=lambda s: s.created_at or datetime.min)
            if not cands:
                continue
            _apply_to(cands[0], e)
            ambig_applied += 1

        # Create stub surgeries for unmatched Calendly rows (skippable)
        import hashlib
        created = 0
        if args.no_create_stubs:
            print(f"\n(Skipping stub creation for {len(unmatched)} unmatched rows per --no-create-stubs)")
            db.commit()
            print(f"\n✓ Applied appointment time + facility to {applied} matched surgeries.")
            print(f"✓ Applied to {ambig_applied} ambiguous (oldest candidate kept, duplicates untouched).")
            return
        for e in unmatched:
            r = e["csv_row"]
            event_type = (r.get("Event Type Name") or "").strip()
            classification = EVENT_TO_CLASSIFICATION.get(event_type)
            full = (r.get("Invitee Name") or "").strip()
            first = (r.get("Invitee First Name") or "").strip() or None
            last = (r.get("Invitee Last Name") or "").strip() or None
            email = (r.get("Invitee Email") or "").strip().lower() or None
            phone = (r.get("Text Reminder Number") or "").strip() or None
            fac = e["facility"]
            start_dt = e["start_dt"]

            # Stub chart number — deterministic per email (so re-running the
            # script doesn't produce ever-growing duplicates of the same patient).
            # Staff overwrites with the real chart # from EHR after intake.
            stub_key = (email or full or str(start_dt)).lower()
            stub_hash = hashlib.sha1(stub_key.encode("utf-8")).hexdigest()[:6].upper()
            chart_stub = f"TBD-{stub_hash}"

            s = Surgery(
                chart_number=chart_stub,
                patient_name=full,
                first_name=first,
                last_name=last,
                email=email,
                phone=phone,
                cell_phone=phone,
                scheduled_date=start_dt.date(),
                scheduled_start_time=start_dt.time(),
                selected_facility=fac,
                eligible_facilities=[fac] if fac else [],
                procedure_classification=classification,
                estimated_minutes=EVENT_DURATION.get(classification),
                status="incomplete",
                source="calendly",
                notes=f"Auto-created from Calendly import on {datetime.utcnow().date()} "
                      f"(event_type={event_type}). Fill in real chart # (currently {chart_stub}), "
                      f"DOB, procedure, insurance from EHR.",
            )
            db.add(s)
            created += 1

        db.commit()
        print(f"\n✓ Applied appointment time + facility to {applied} matched surgeries.")
        print(f"✓ Applied to {ambig_applied} ambiguous (oldest candidate kept, duplicates untouched).")
        print(f"✓ Created {created} stub surgeries (status=incomplete) from unmatched rows.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
