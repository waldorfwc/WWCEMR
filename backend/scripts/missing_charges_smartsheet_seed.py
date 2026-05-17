"""Seed Missing Charges from Smartsheet 'Appointments Missing Charges'
(sheet 5904915281956740).

Each Smartsheet row is a single appointment. We:
  1. Match by (patient_mrn, appointment_date) — skip if already present.
  2. Map the Smartsheet 'Status' picklist into our workflow vocab:
       'Already Billed'      → 'billed'
       'Needs to be billed'  → 'needs_to_be_billed'
       'Marked Canceled'     → 'canceled'
  3. Preserve the ModMed deep link (Patient Link).

Run with no flag = dry-run. Pass --apply to commit.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date as _date, datetime
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

from app.database import SessionLocal, init_db
from app.models.missing_charge import (
    MissingCharge, MissingChargeImport, TERMINAL_STATUSES,
)


SHEET_ID = "5904915281956740"


SMARTSHEET_STATUS_MAP = {
    "already billed":     "billed",
    "needs to be billed": "needs_to_be_billed",
    "marked canceled":    "canceled",
    "marked cancelled":   "canceled",
}


def parse_date(v) -> Optional[_date]:
    if not v:
        return None
    if isinstance(v, _date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def fetch_sheet(token: str) -> dict:
    r = requests.get(f"https://api.smartsheet.com/2.0/sheets/{SHEET_ID}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def build_row_dict(row: dict, col_lookup: dict) -> dict:
    out: dict = {}
    for cell in row.get("cells", []):
        col_id = cell.get("columnId")
        title = col_lookup.get(col_id)
        if not title:
            continue
        v = cell.get("displayValue") or cell.get("value")
        if v in (None, ""):
            continue
        if title not in out:
            out[title] = v
    return out


def get_token() -> str:
    token = os.environ.get("SMARTSHEET_TOKEN")
    if token:
        return token
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("SMARTSHEET_TOKEN="):
                    return line.split("=", 1)[1].strip()
    sys.exit("SMARTSHEET_TOKEN not configured")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        token = get_token()
        sheet = fetch_sheet(token)
        col_lookup = {c["id"]: c["title"] for c in sheet["columns"]}

        # Create one import record to attach all seeded rows to
        imp = MissingChargeImport(
            original_filename=f"smartsheet:{SHEET_ID}",
            uploaded_by="system:smartsheet-seed",
            total_rows=len(sheet.get("rows", [])),
            notes="Backfill from Smartsheet 'Appointments Missing Charges'",
        )
        db.add(imp); db.flush()

        n_added = n_skipped_dup = n_skipped_bad = 0
        status_dist: dict = {}

        for raw in sheet.get("rows", []):
            r = build_row_dict(raw, col_lookup)
            mrn = (r.get("Patient MRN") or "").strip()
            dos = parse_date(r.get("Appointment Date"))
            if not mrn or not dos:
                n_skipped_bad += 1
                continue

            existing = (db.query(MissingCharge)
                          .filter(MissingCharge.patient_mrn == mrn,
                                  MissingCharge.appointment_date == dos)
                          .first())
            if existing:
                n_skipped_dup += 1
                continue

            sheet_status = (r.get("Status") or "").strip().lower()
            our_status = SMARTSHEET_STATUS_MAP.get(sheet_status, "new")
            status_dist[our_status] = status_dist.get(our_status, 0) + 1

            resolved_at = None
            resolved_by = None
            if our_status in TERMINAL_STATUSES:
                resolved_at = datetime.utcnow()
                resolved_by = "system:smartsheet-seed"

            db.add(MissingCharge(
                source_import_id=imp.id,
                patient_mrn=mrn,
                appointment_date=dos,
                patient_name=(r.get("Patient Name") or "").strip() or None,
                appointment_type=(r.get("Appointment Type") or "").strip() or None,
                appointment_status=(r.get("Appointment Status") or "").strip() or None,
                visit_status=(r.get("Visit Status") or "").strip() or None,
                payer=(r.get("Payer") or "").strip() or None,
                primary_provider=(r.get("Primary Provider") or "").strip() or None,
                patient_link=(r.get("Patient Link") or "").strip() or None,
                status=our_status,
                resolved_at=resolved_at,
                resolved_by=resolved_by,
            ))
            n_added += 1

        imp.new_rows = n_added
        imp.duplicate_rows = n_skipped_dup
        imp.error_rows = n_skipped_bad

        print(f"\nSmartsheet rows scanned: {len(sheet.get('rows', []))}")
        print(f"  Rows added:        {n_added}")
        print(f"  Skipped — dup:     {n_skipped_dup}")
        print(f"  Skipped — bad:     {n_skipped_bad}")
        print(f"\nStatus distribution of new rows:")
        for k in sorted(status_dist):
            print(f"  {k:24s}  {status_dist[k]}")

        if not args.apply:
            db.rollback()
            print("\nDRY RUN — rolled back. Re-run with --apply to commit.")
        else:
            db.commit()
            print("\n✓ Committed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
