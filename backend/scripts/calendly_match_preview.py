"""Dry-run match of a Calendly CSV against the Surgery table.

For each row:
  - Email match → surgery
      * If surgery has no scheduled_date → APPLY candidate
      * If surgery already has a scheduled_date → SKIP (already scheduled)
  - No email match → fuzzy name match (Postgres % similarity)
      * ≥ 0.90 ratio AND surgery has no scheduled_date → REVIEW candidate
      * else → NO MATCH (or already dated)

Prints a structured report; does NOT mutate anything.

Run with DATABASE_URL set:
  DATABASE_URL=postgresql://postgres:<pw>@127.0.0.1:5433/wwc_app \
      venv/bin/python -m scripts.calendly_match_preview /path/to/Calendly.csv
"""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from difflib import SequenceMatcher

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


FACILITY_FROM_EVENT = {
    "CRMC_Minor":     "crmc",
    "CRMC_Major":     "crmc",
    "MedStar_Rbt_2c": "medstar",
    "White_Plains":   "office",
}


def parse_dt(s: str) -> datetime:
    s = s.strip()
    # Accept "6/9/26 9:30" and "6/9/26 09:30"
    for fmt in ("%m/%d/%y %H:%M", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"could not parse datetime: {s}")


def norm_name(s: str) -> str:
    return " ".join((s or "").lower().strip().split())


def main(csv_path: str) -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERR: DATABASE_URL not set", file=sys.stderr); sys.exit(2)

    eng = create_engine(db_url)
    Session = sessionmaker(bind=eng)
    sess = Session()

    # Load all surgeries that are still "open" (not cancelled or completed).
    # We need email + patient_name + chart + scheduled_date.
    rows = sess.execute(text("""
        SELECT id, patient_name, chart_number, lower(email) AS email,
               scheduled_date, status, selected_facility
          FROM surgeries
         WHERE status NOT IN ('cancelled', 'completed')
    """)).mappings().all()

    by_email: dict[str, list[dict]] = {}
    all_surgeries: list[dict] = []
    for r in rows:
        d = dict(r)
        all_surgeries.append(d)
        em = (d.get("email") or "").strip()
        if em:
            by_email.setdefault(em, []).append(d)

    # Tag each surgery with a normalized name for fuzzy matching
    for s in all_surgeries:
        s["_n"] = norm_name(s.get("patient_name") or "")

    apply_rows: list[dict] = []        # email match, no scheduled_date
    skip_dated: list[dict] = []        # email match but already dated
    review_rows: list[dict] = []       # fuzzy name match
    no_match: list[dict] = []          # no email + no fuzzy

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = (row.get("Invitee Email") or "").strip().lower()
            first = (row.get("Invitee First Name") or "").strip()
            last  = (row.get("Invitee Last Name") or "").strip()
            event = (row.get("Event Type Name") or "").strip()
            start = parse_dt(row.get("Start Date & Time") or "")
            facility = FACILITY_FROM_EVENT.get(event)

            entry = {
                "csv_name":  f"{first} {last}".strip(),
                "csv_email": email,
                "csv_event": event,
                "csv_start": start,
                "csv_facility": facility,
            }

            # 1. Email match path
            matches = by_email.get(email, [])
            if matches:
                # Choose the matched row — prefer one without scheduled_date
                m = next((m for m in matches if m["scheduled_date"] is None), matches[0])
                entry["surgery_id"]      = str(m["id"])
                entry["chart_number"]    = m["chart_number"]
                entry["surgery_name"]    = m["patient_name"]
                entry["surgery_status"]  = m["status"]
                entry["current_date"]    = m["scheduled_date"]
                entry["matched_by"]      = "email"
                if m["scheduled_date"] is None:
                    apply_rows.append(entry)
                else:
                    skip_dated.append(entry)
                continue

            # 2. Fuzzy name match (≥0.90 SequenceMatcher ratio)
            target_norm = norm_name(f"{last}, {first}")
            target_alt  = norm_name(f"{first} {last}")
            best = None
            best_score = 0.0
            for s in all_surgeries:
                ratio = max(
                    SequenceMatcher(None, target_norm, s["_n"]).ratio(),
                    SequenceMatcher(None, target_alt,  s["_n"]).ratio(),
                )
                if ratio > best_score:
                    best_score = ratio
                    best = s
            if best and best_score >= float(os.environ.get("FUZZY_THRESHOLD", "0.90")):
                entry["surgery_id"]     = str(best["id"])
                entry["chart_number"]   = best["chart_number"]
                entry["surgery_name"]   = best["patient_name"]
                entry["surgery_status"] = best["status"]
                entry["current_date"]   = best["scheduled_date"]
                entry["matched_by"]     = f"name ({best_score:.2f})"
                if best["scheduled_date"] is None:
                    review_rows.append(entry)
                else:
                    skip_dated.append(entry)
            else:
                entry["best_name"]  = best["patient_name"] if best else None
                entry["best_score"] = round(best_score, 2)
                no_match.append(entry)

    # Report
    def line(items, header):
        print(f"\n{'='*78}\n{header}  ({len(items)} row{'s' if len(items)!=1 else ''})\n{'='*78}")
        for r in items:
            d = r["csv_start"].strftime("%m/%d/%Y %H:%M")
            tag = f"({r.get('matched_by')})" if r.get("matched_by") else ""
            existing = f"  current_date={r['current_date']}" if r.get("current_date") else ""
            print(f"  • {r['csv_name']:<28} {r['csv_email']:<35} {d} {r['csv_facility']:<8} "
                   f"{tag}{existing}")
            if "chart_number" in r:
                print(f"      → chart {r['chart_number']}  status={r['surgery_status']}  "
                       f"surgery_name={r['surgery_name']}")
            if r in no_match and r.get("best_name"):
                print(f"      best fuzzy: {r['best_name']} ({r['best_score']})")

    line(apply_rows,  "APPLY  (email match, no date yet)")
    line(review_rows, "REVIEW (name match ≥ 90%, no date yet) — confirm before applying")
    line(skip_dated,  "SKIP   (matched but already has a scheduled_date)")
    line(no_match,    "NO MATCH (email + fuzzy both failed)")

    print(f"\nSUMMARY: apply={len(apply_rows)}  review={len(review_rows)}  "
           f"skip_dated={len(skip_dated)}  no_match={len(no_match)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: calendly_match_preview.py <csv_path>", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1])
