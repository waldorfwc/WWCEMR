"""One-time LARC device repopulation from Smartsheet.

Wipes ALL existing LARC inventory + assignment data, then imports fresh
from the 'LARC Devices' sheet (id 4797037909698436).

Smartsheet is the source of truth UNTIL we go live; after go-live we
never sync again, so this script is intentionally one-shot.

Run as:
  python -m backend.scripts.larc_devices_smartsheet_repopulate            # dry-run (default)
  python -m backend.scripts.larc_devices_smartsheet_repopulate --apply    # commits

DRY-RUN prints exactly what would be deleted + created, but does not
touch the database. APPLY commits the wipe + import in a single
transaction; rolls back cleanly on any error.

Ownership classification (per practice rule, 2026-06-02 conversation):
  Smartsheet 'Device Claim' value         → LarcDevice.ownership
  ───────────────────────────────────────   ────────────────────────
  Patient Owned                             patient_owned
  Patient Owned - Replacement               patient_owned
  WWC Purchased                             wwc_owned
  (blank)                                   wwc_owned   ← defensive default

The wwc_claimed bucket isn't populated by this import — we'd flip
specific rows manually after go-live as patients forfeit their devices.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import date as _date, datetime
from typing import Optional

# Allow `python backend/scripts/...` or `python -m backend.scripts...` to work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx

# Importing app.main first registers every model so SQLAlchemy's mapper
# can resolve cross-module string-based relationship() targets like
# SurgeryPayment / Surgery — otherwise db.query(...) fails with
# 'name SurgeryPayment is not defined' from clsregistry.
import app.main  # noqa: F401

from app.database import SessionLocal
from app.models.larc import (
    LarcAssignment, LarcAuditEvent, LarcDevice, LarcDeviceType, LarcMilestone,
)


SHEET_ID = "4797037909698436"
SMARTSHEET_API = "https://api.smartsheet.com/2.0"


# Smartsheet "Device Type" value → seed-table LarcDeviceType.name
DEVICE_TYPE_MAP = {
    "mirena-j7298":     "Mirena",
    "liletta-j7297":    "Liletta",
    "lilleta-j7297":    "Liletta",   # spelled wrong in some rows
    "nexplanon-j7307":  "Nexplanon",
    "paragard-j7300":   "Paragard",
    "kyleena-j7296":    "Kyleena",
    "skyla-j7301":      "Skyla",
}

# Smartsheet "Device Claim" → LarcDevice.ownership
CLAIM_TO_OWNERSHIP = {
    "patient owned":              "patient_owned",
    "patient owned - replacement": "patient_owned",
    "wwc purchased":              "wwc_owned",
}

# Smartsheet "Location" → LarcDevice.location (must be one of LOCATIONS)
LOCATION_MAP = {
    "billing office":                       "white_plains",
    "billing storage":                      "white_plains",
    "brandywine-storage":                   "brandywine",
    "brandywine storage":                   "brandywine",
    "in transport to brandywine storage":   "brandywine",
    "white plains":                         "white_plains",
    "arlington":                            "arlington",
}


def _auth_headers() -> dict:
    token = os.environ.get("SMARTSHEET_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SMARTSHEET_TOKEN not set in environment")
    return {"Authorization": f"Bearer {token}"}


def _fetch_sheet() -> dict:
    r = httpx.get(f"{SMARTSHEET_API}/sheets/{SHEET_ID}",
                  headers=_auth_headers(), timeout=60)
    r.raise_for_status()
    return r.json()


def _row_to_dict(row: dict, columns_by_id: dict[int, str]) -> dict:
    out: dict = {}
    for cell in row.get("cells", []):
        title = columns_by_id.get(cell.get("columnId"))
        if not title:
            continue
        val = cell.get("displayValue", cell.get("value"))
        if val in (None, "", "-"):
            continue
        out[title] = val
    out["_row_id"] = row.get("id")
    return out


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _parse_date(s) -> Optional[_date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "")).date()
    except Exception:
        return None


def _classify(row: dict) -> tuple[str, str]:
    """Returns (ownership, device_type_name) — both required."""
    claim_raw = _norm(row.get("Device Claim"))
    ownership = CLAIM_TO_OWNERSHIP.get(claim_raw, "wwc_owned")

    dt_raw = _norm(row.get("Device Type"))
    type_name = DEVICE_TYPE_MAP.get(dt_raw)
    return ownership, type_name


def main(apply: bool):
    db = SessionLocal()

    print(f"--- LARC repopulation from Smartsheet {SHEET_ID} ---")
    print(f"Mode: {'APPLY (will commit)' if apply else 'DRY-RUN (no DB changes)'}")
    print()

    sheet = _fetch_sheet()
    cols = {c["id"]: c["title"] for c in sheet["columns"]}
    rows = [_row_to_dict(r, cols) for r in sheet.get("rows", [])]
    print(f"Smartsheet rows fetched: {len(rows)}")

    # Validate device types up front
    type_by_name = {t.name: t for t in db.query(LarcDeviceType).all()}
    missing_types: Counter = Counter()
    skipped_rows: list[str] = []
    plan: list[dict] = []   # what we'd insert

    for row in rows:
        asset_tag = (row.get("Asset Tag #") or "").strip()
        if not asset_tag:
            skipped_rows.append(f"row {row['_row_id']}: missing Asset Tag #")
            continue

        ownership, type_name = _classify(row)
        if not type_name:
            missing_types[row.get("Device Type") or "(blank)"] += 1
            skipped_rows.append(f"row {row['_row_id']} ({asset_tag}): "
                                f"unknown device type {row.get('Device Type')!r}")
            continue
        dt = type_by_name.get(type_name)
        if not dt:
            skipped_rows.append(f"row {row['_row_id']} ({asset_tag}): "
                                f"device type '{type_name}' not seeded yet")
            continue

        location_raw = _norm(row.get("Location"))
        location = LOCATION_MAP.get(location_raw, "white_plains")

        # Insertion / billing decide the status to assign
        ins_status = _norm(row.get("Insertion Status"))
        billed = _norm(row.get("Device Billed"))
        if "yes" in billed:
            status = "billed"
        elif "inserted" == ins_status:
            status = "inserted"
        elif row.get("Assigned To : Patient ID"):
            status = "assigned"
        else:
            status = "unassigned"

        # Optional assignment
        assn = None
        assigned_chart = (row.get("Assigned To : Patient ID") or "").strip()
        if assigned_chart:
            assn = {
                "chart_number": assigned_chart,
                "patient_name": (row.get("Patient Name")
                                  or f"{row.get('Assigned Last Name: ', '')}, "
                                     f"{row.get('Assigned To: First Name', '')}").strip(", "),
                "assigned_date": _parse_date(row.get("Assigned Date")),
                "billed_at":     _parse_date(row.get("Billed On")),
                "inserted_at":   _parse_date(row.get("Appt Date")),
            }

        plan.append({
            "our_id":              asset_tag,
            "device_type":         type_name,
            "device_type_id":      dt.id,
            "manufacturer_lot":    (row.get("Lot #") or None),
            "expiration_date":     _parse_date(row.get("Expiration Date")),
            "purchase_date":       _parse_date(row.get("Device Arrived Date")),
            "location":            location,
            "status":              status,
            "ownership":           ownership,
            "purchasing_patient_chart": (row.get("Purchasing Patient ID") or None),
            "purchasing_patient_name":
                (row.get("Patient Name") if ownership == "patient_owned" else None),
            "assignment":          assn,
        })

    # --- Summaries ----------------------------------------------------
    by_type = Counter(p["device_type"] for p in plan)
    by_own = Counter(p["ownership"] for p in plan)
    by_status = Counter(p["status"] for p in plan)
    by_loc = Counter(p["location"] for p in plan)
    will_assign = sum(1 for p in plan if p["assignment"])

    print()
    print(f"Devices to import:  {len(plan)}")
    print(f"   by type:        {dict(by_type)}")
    print(f"   by ownership:   {dict(by_own)}")
    print(f"   by status:      {dict(by_status)}")
    print(f"   by location:    {dict(by_loc)}")
    print(f"   with assignment: {will_assign}")
    if skipped_rows:
        print(f"\nSkipped {len(skipped_rows)} rows:")
        for s in skipped_rows[:15]:
            print(f"   - {s}")
        if len(skipped_rows) > 15:
            print(f"   ... and {len(skipped_rows) - 15} more")
    if missing_types:
        print(f"\nUnmapped Device Type values:")
        for k, n in missing_types.items():
            print(f"   - {k!r}: {n}")

    # --- Wipe + insert ------------------------------------------------
    existing_devices = db.query(LarcDevice).count()
    existing_assns   = db.query(LarcAssignment).count()
    existing_milestones = db.query(LarcMilestone).count()
    existing_audit   = db.query(LarcAuditEvent).count()
    print()
    print("Existing rows that will be wiped:")
    print(f"   LarcDevice:      {existing_devices}")
    print(f"   LarcAssignment:  {existing_assns}")
    print(f"   LarcMilestone:   {existing_milestones}")
    print(f"   LarcAuditEvent:  {existing_audit}")

    if not apply:
        print("\nDRY-RUN — no changes committed.")
        print("Re-run with --apply to wipe + import for real.")
        db.close()
        return

    # APPLY
    print("\nAPPLYING — wiping existing data...")
    db.query(LarcAuditEvent).delete()
    db.query(LarcMilestone).delete()
    db.query(LarcAssignment).delete()
    db.query(LarcDevice).delete()
    db.flush()

    inserted_devices = 0
    inserted_assns = 0
    for p in plan:
        d = LarcDevice(
            our_id=p["our_id"],
            manufacturer_lot=p["manufacturer_lot"],
            device_type_id=p["device_type_id"],
            expiration_date=p["expiration_date"],
            purchase_date=p["purchase_date"],
            location=p["location"],
            status=p["status"],
            ownership=p["ownership"],
            purchasing_patient_chart=p["purchasing_patient_chart"],
            purchasing_patient_name=p["purchasing_patient_name"],
            notes="Imported from Smartsheet on " + datetime.utcnow().date().isoformat(),
        )
        db.add(d); db.flush()
        inserted_devices += 1

        if p["assignment"]:
            a = LarcAssignment(
                device_id=d.id,
                chart_number=p["assignment"]["chart_number"],
                patient_name=p["assignment"]["patient_name"] or "",
                source_flow="in_stock",
                status=p["status"] if p["status"] in ("inserted", "billed") else "device_received",
            )
            if p["assignment"]["billed_at"]:
                a.billed_at = datetime.combine(
                    p["assignment"]["billed_at"], datetime.min.time())
            db.add(a); db.flush()
            inserted_assns += 1

    db.commit()
    db.close()
    print(f"\n✓ Inserted {inserted_devices} devices, {inserted_assns} assignments.")
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Commit the wipe + import. Without this it's a dry-run.")
    args = parser.parse_args()
    main(apply=args.apply)
