"""Seed office-procedure devices (NovaSure, Bensta) from Smartsheet
'#2 -All Surgery Devices in office' (sheet 1710608391032708).

Each row is a single physical device, optionally bound to a patient/surgery.
We:

  1. Match the row to a LarcDevice by Asset Tag → our_id. Create if missing.
  2. If Patient ID + name present, create a LarcAssignment with
     source_flow='office_procedure' and auto-complete its milestones from
     the row's date fields (Signed out Date → device_assigned/consumed,
     Billed On → billed).

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
from app.models.larc import (
    LarcAssignment, LarcDevice, LarcDeviceType,
)
from app.services.larc_workflow import log_audit, spawn_milestones


SHEET_ID = "1710608391032708"


# Smartsheet device-type label → our seed-table name
DEVICE_TYPE_MAP = {
    "benesta pro (caldera)": "Bensta",
    "benesta pro":           "Bensta",
    "benesta":               "Bensta",
    "novasure (hologic)":    "NovaSure",
    "novasure":              "NovaSure",
}


LOCATION_MAP = {
    "white plains office":   "white_plains",
    "white plains":          "white_plains",
    "white plains storage":  "white_plains",
    "white plains - storage": "white_plains",
    "brandywine office":     "brandywine",
    "brandywine":            "brandywine",
    "brandywine storage":    "brandywine",
    "arlington office":      "arlington",
    "arlington":             "arlington",
    "arlington storage":     "arlington",
}


def map_location(s: Optional[str]) -> str:
    if not s:
        return "white_plains"
    return LOCATION_MAP.get(s.strip().lower(), "white_plains")


def parse_date(v) -> Optional[_date]:
    if not v:
        return None
    if isinstance(v, _date):
        return v
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
        # Some columns repeat the same title; keep first non-empty value
        val = cell.get("displayValue") or cell.get("value")
        if val in (None, ""):
            continue
        if title not in out:
            out[title] = val
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

        device_types = {t.name: t for t in db.query(LarcDeviceType).all()}

        n_devices_added = 0
        n_devices_updated = 0
        n_assignments_added = 0
        n_skipped_no_tag = 0
        n_skipped_unknown_type = 0

        rows = sheet.get("rows", [])
        for raw in rows:
            r = build_row_dict(raw, col_lookup)
            tag = (r.get("Asset Tag #") or "").strip()
            device_type_raw = (r.get("Device Type") or "").strip()
            lot = (r.get("Lot #") or "").strip() or None
            expiry = parse_date(r.get("Expiration Date"))
            arrived = parse_date(r.get("Arrived Date"))
            location = map_location(r.get("Location"))
            available = (r.get("Available") or "").strip().lower()

            # Patient / surgery info
            patient_id = (r.get("Patient ID") or "").strip()
            first = (r.get("First Name") or "").strip()
            last = (r.get("Last Name") or "").strip()
            full_name = (r.get("Full Name") or "").strip()
            surgery_date = parse_date(r.get("SURGERY DATE"))
            signed_out_date = parse_date(r.get("Signed out Date"))
            insertion_status = (r.get("Insertion Status") or "").strip().lower()
            billed_on = parse_date(r.get("Billed On"))
            device_returned_date = parse_date(r.get("Device Return Date"))
            confirmed_returned_date = parse_date(r.get("Confirmed Returned Date"))
            device_updated_status = (r.get("Device updated status") or "").strip().lower()

            if not tag:
                n_skipped_no_tag += 1
                continue

            mapped_name = DEVICE_TYPE_MAP.get(device_type_raw.lower())
            dt = device_types.get(mapped_name) if mapped_name else None
            if not dt:
                n_skipped_unknown_type += 1
                print(f"  ! Unknown device type {device_type_raw!r} for tag {tag} — skipping")
                continue

            # ── upsert the device ──
            existing = db.query(LarcDevice).filter(LarcDevice.our_id == tag).first()

            # Decide a status for the device
            if billed_on or "billed" in device_updated_status:
                dev_status = "billed"
            elif insertion_status in ("inserted", "completed", "consumed", "used"):
                dev_status = "inserted"
            elif device_returned_date or confirmed_returned_date or "returned" in device_updated_status:
                dev_status = "returned"
            elif "lost" in device_updated_status:
                dev_status = "lost"
            elif "expired" in device_updated_status or (
                expiry and expiry < _date.today() and not patient_id
            ):
                dev_status = "expired"
            elif patient_id and (signed_out_date or surgery_date):
                dev_status = "assigned"
            elif available == "yes":
                dev_status = "unassigned"
            else:
                dev_status = "unassigned"

            if existing:
                if lot and not existing.manufacturer_lot:
                    existing.manufacturer_lot = lot
                if expiry and not existing.expiration_date:
                    existing.expiration_date = expiry
                if arrived and not existing.purchase_date:
                    existing.purchase_date = arrived
                if location != existing.location:
                    existing.location = location
                # Only override status if the row gives a more advanced state
                advance = ("billed", "inserted", "returned", "lost", "expired")
                if dev_status in advance and existing.status not in advance:
                    existing.status = dev_status
                device = existing
                n_devices_updated += 1
            else:
                device = LarcDevice(
                    our_id=tag,
                    device_type_id=dt.id,
                    manufacturer_lot=lot,
                    expiration_date=expiry,
                    purchase_date=arrived,
                    location=location,
                    status=dev_status,
                )
                db.add(device); db.flush()
                n_devices_added += 1

            # ── create assignment if patient info present ──
            has_patient = bool(patient_id and (full_name or last or first))
            if not has_patient:
                if not existing:
                    log_audit(db, actor="system:smartsheet-seed-op",
                              action="device_added", device=device,
                              summary=f"Seeded {dt.name} #{tag} (no patient) from Smartsheet OP")
                continue

            # Skip if any assignment already exists for this device
            existing_a = (db.query(LarcAssignment)
                            .filter(LarcAssignment.device_id == device.id)
                            .first())
            if existing_a:
                continue

            # Build patient name "Last, First"
            if last and first:
                patient_name = f"{last}, {first}"
            elif full_name:
                # Smartsheet stores "Last,First" or "Last, First"
                if "," in full_name and ", " not in full_name:
                    parts = full_name.split(",", 1)
                    patient_name = f"{parts[0].strip()}, {parts[1].strip()}"
                else:
                    patient_name = full_name
            else:
                patient_name = last or first or "Unknown"

            # Map assignment status
            if billed_on:
                a_status = "billed"
            elif insertion_status in ("inserted", "completed", "consumed", "used"):
                a_status = "inserted"
            elif insertion_status in ("no show", "patient no show"):
                a_status = "patient_no_show"
            elif insertion_status in ("cancelled by patient", "patient canceled"):
                a_status = "patient_canceled"
            elif insertion_status in ("cancelled by office", "office canceled"):
                a_status = "office_canceled"
            else:
                a_status = "in_progress"

            a = LarcAssignment(
                device_id=device.id,
                chart_number=patient_id,
                patient_name=patient_name,
                source_flow="office_procedure",
                status=a_status,
                is_active=a_status not in ("billed", "cancelled"),
                appt_date=surgery_date,
                inserted_at=(
                    datetime.combine(surgery_date or signed_out_date, datetime.min.time())
                    if a_status == "inserted" and (surgery_date or signed_out_date) else None
                ),
                billed_at=(
                    datetime.combine(billed_on, datetime.min.time())
                    if billed_on else None
                ),
                notes=f"Seeded from Smartsheet {SHEET_ID} (office-procedure)",
            )
            db.add(a); db.flush()
            spawn_milestones(db, a)

            # Auto-complete milestones from row state
            ms = {m.kind: m for m in a.milestones}
            def mark(kind, when=None, by="system:smartsheet-seed-op"):
                m = ms.get(kind)
                if m and m.status == "pending":
                    m.status = "done"
                    m.completed_at = (datetime.combine(when, datetime.min.time())
                                      if isinstance(when, _date) else (when or datetime.utcnow()))
                    m.completed_by = by

            # device_assigned: completed at signed-out OR surgery-date OR assignment creation
            mark("device_assigned", signed_out_date or surgery_date)
            if a_status in ("inserted", "billed"):
                mark("device_consumed", surgery_date or signed_out_date)
            if billed_on:
                mark("billed", billed_on)

            log_audit(db, actor="system:smartsheet-seed-op",
                      action="op_assignment_created",
                      device=device, assignment=a,
                      summary=(f"Seeded {dt.name} #{tag} for {patient_name} "
                               f"(status={a_status}) from Smartsheet OP"))
            n_assignments_added += 1

        print(f"\nSmartsheet rows: {len(rows)}")
        print(f"  Devices created:        {n_devices_added}")
        print(f"  Devices updated:        {n_devices_updated}")
        print(f"  Assignments created:    {n_assignments_added}")
        print(f"  Skipped — no Asset Tag: {n_skipped_no_tag}")
        print(f"  Skipped — unknown type: {n_skipped_unknown_type}")

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
