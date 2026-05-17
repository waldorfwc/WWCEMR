"""Seed the LARC module from the WWC Smartsheet (LARC Devices, ID 4797037909698436).

Each Smartsheet row represents either a device-only record (in stock,
no patient yet) or a device + patient assignment. We:

  1. Match the Smartsheet device row to a LarcDevice by Asset Tag → our_id.
     Create if missing.
  2. If the row has a patient (Patient Name + Patient ID), match-or-create
     a LarcAssignment.
  3. Set milestone timestamps from the row's date fields.

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
    LarcAssignment, LarcAuditEvent, LarcDevice, LarcDeviceType, LarcMilestone,
)
from app.services.larc_workflow import log_audit, spawn_milestones


DEFAULT_SHEET_ID = "4797037909698436"

# Insertion Status values seen in the archive sheet beyond the live sheet:
#   "Failed Insertion - Device not Opened"  → failed_unused
#   "Failed Insertion - Device Opened"      → failed_used
#   "Not Used/Opened - Appt Cancelled"      → patient_canceled
#   "EXPIRED-DISCARDED"                     → device.status = expired
#   "Returned Qualanx"                      → returned

def _norm(s: str) -> str:
    return (s or "").strip().lower()


def map_insertion_status(raw: str) -> str:
    """Return one of: inserted | failed_used | failed_unused | patient_no_show
    | patient_canceled | office_canceled | expired | returned | ''."""
    s = _norm(raw)
    if "inserted" in s or s == "completed":
        return "inserted"
    if "failed" in s and "opened" in s and "not opened" not in s:
        return "failed_used"
    if ("failed" in s and "not opened" in s) or "failed - unused" in s:
        return "failed_unused"
    if "not used" in s and "cancel" in s:
        return "patient_canceled"
    if "no show" in s:
        return "patient_no_show"
    if "cancelled by office" in s or "office canceled" in s:
        return "office_canceled"
    if "cancelled by patient" in s or "patient canceled" in s:
        return "patient_canceled"
    if "expired" in s:
        return "expired"
    if "returned" in s:
        return "returned"
    return ""


def is_billed(raw: str) -> bool:
    s = _norm(raw)
    if not s:
        return False
    if "expired" in s or "not billed" in s:
        return False
    return s in ("yes", "y", "true", "yes-billed", "device billed") or "billed" in s and "not" not in s


LOCATION_MAP = {
    "brandywine-storage": "brandywine",
    "brandywine": "brandywine",
    "white plains": "white_plains",
    "white plains storage": "white_plains",
    "white plains - storage": "white_plains",
    "white plains-storage": "white_plains",
    "arlington": "arlington",
    "arlington storage": "arlington",
    "arlington-storage": "arlington",
    "billing storage": "white_plains",   # billing kept at HQ
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


def fetch_sheet(token: str, sheet_id: str) -> dict:
    r = requests.get(f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def build_row_dict(row: dict, col_lookup: dict) -> dict:
    """Map Smartsheet row cells into a name-keyed dict."""
    out: dict = {}
    for cell in row.get("cells", []):
        col_id = cell.get("columnId")
        title = col_lookup.get(col_id)
        if not title:
            continue
        out[title] = cell.get("displayValue") or cell.get("value")
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
    ap.add_argument("--sheet-id", default=DEFAULT_SHEET_ID,
                    help=f"Smartsheet ID (default: {DEFAULT_SHEET_ID})")
    ap.add_argument("--include-tagless", action="store_true",
                    help="Mint synthetic HIST-<row_id> our_ids for rows with no Asset Tag "
                         "(useful for the archive sheet 8743498875725700)")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        token = get_token()
        sheet = fetch_sheet(token, args.sheet_id)
        col_lookup = {c["id"]: c["title"] for c in sheet["columns"]}

        # Build device-type name → row map (case-insensitive)
        device_types = {t.name.lower(): t for t in db.query(LarcDeviceType).all()}

        n_devices_added = 0
        n_devices_updated = 0
        n_assignments_added = 0
        n_skipped_no_tag = 0
        n_skipped_unknown_type = 0

        rows = sheet.get("rows", [])
        n_tagless_synthesized = 0
        for raw in rows:
            r = build_row_dict(raw, col_lookup)
            tag = (r.get("Asset Tag #") or "").strip()
            if not tag and args.include_tagless:
                # Mint a synthetic, sheet-stable our_id for historical rows.
                # Smartsheet row IDs are stable across re-runs, so reruns are
                # idempotent — same row → same HIST-* id.
                tag = f"HIST-{raw['id']}"
                n_tagless_synthesized += 1
            device_type_name = (r.get("Device Type") or "").strip()
            lot = (r.get("Lot #") or "").strip() or None
            expiry = parse_date(r.get("Expiration Date"))
            arrived = parse_date(r.get("Device Arrived Date")) or parse_date(r.get("Date Received"))
            location = map_location(r.get("Location"))
            device_claim = (r.get("Device Claim") or "").strip()
            patient_name_raw = (r.get("Patient Name") or r.get("Full Name") or "").strip()
            patient_id = (r.get("Assigned To : Patient ID")
                          or r.get("Purchasing Patient ID")
                          or "").strip()
            assigned_date = parse_date(r.get("Assigned Date"))
            insertion_status = (r.get("Insertion Status") or "").strip().lower()
            appt_date = parse_date(r.get("Appt Date"))
            billed = (r.get("Device Billed") or "").strip().lower()
            billed_on = parse_date(r.get("Billed On"))
            signed_out_date = parse_date(r.get("Signed out Date"))
            provider = (r.get("Provider") or "").strip() or None

            if not tag:
                n_skipped_no_tag += 1
                continue

            # Smartsheet appends "-Jxxxx" CPT suffix; strip it for matching.
            # Also alias common misspellings (Lilleta → Liletta).
            stripped = device_type_name.split("-")[0].strip()
            aliases = {"lilleta": "liletta"}
            normalized = aliases.get(stripped.lower(), stripped.lower())
            dt = (device_types.get(device_type_name.lower())
                   or device_types.get(stripped.lower())
                   or device_types.get(normalized))
            if not dt:
                n_skipped_unknown_type += 1
                print(f"  ! Unknown device type '{device_type_name}' for tag {tag} — skipping")
                continue

            # ── upsert the device ──
            existing = db.query(LarcDevice).filter(LarcDevice.our_id == tag).first()
            if existing:
                # Update only empty fields, don't trample
                if lot and not existing.manufacturer_lot:
                    existing.manufacturer_lot = lot
                if expiry and not existing.expiration_date:
                    existing.expiration_date = expiry
                if arrived and not existing.purchase_date:
                    existing.purchase_date = arrived
                if location != existing.location:
                    existing.location = location
                device = existing
                n_devices_updated += 1
            else:
                # Map device status from the row
                ins_status = map_insertion_status(insertion_status)
                if is_billed(billed):
                    status = "billed"
                elif ins_status == "inserted":
                    status = "inserted"
                elif ins_status == "failed_used":
                    status = "defective"
                elif ins_status == "returned":
                    status = "returned"
                elif ins_status == "expired":
                    status = "expired"
                elif ins_status == "failed_unused":
                    # Historical: device opened/unopened but the patient
                    # appointment didn't yield an insertion. For tagless
                    # archive rows force a terminal state so they don't
                    # show on the on-hand dashboard.
                    status = "lost" if tag.startswith("HIST-") else "unassigned"
                elif ins_status == "patient_canceled":
                    status = "lost" if tag.startswith("HIST-") else "unassigned"
                elif patient_name_raw and assigned_date:
                    status = "assigned"
                else:
                    status = "unassigned"

                device = LarcDevice(
                    our_id=tag,
                    device_type_id=dt.id,
                    manufacturer_lot=lot,
                    expiration_date=expiry,
                    purchase_date=arrived,
                    location=location,
                    status=status,
                )
                db.add(device); db.flush()
                n_devices_added += 1

            # ── create assignment if patient info present ──
            if patient_name_raw and patient_id:
                # Skip if assignment already exists for this device
                existing_a = (db.query(LarcAssignment)
                                .filter(LarcAssignment.device_id == device.id)
                                .first())
                if existing_a:
                    continue

                # Normalize "Last,First" → "Last, First"
                if "," in patient_name_raw and ", " not in patient_name_raw:
                    parts = patient_name_raw.split(",", 1)
                    patient_name = f"{parts[0].strip()}, {parts[1].strip()}"
                else:
                    patient_name = patient_name_raw

                source_flow = ("in_stock" if "wwc purchased" in device_claim.lower()
                                else "pharmacy_order")

                # Derive assignment status from sheet values
                ins_a_status = map_insertion_status(insertion_status)
                if is_billed(billed):
                    a_status = "billed"
                elif ins_a_status:
                    # inserted / failed_used / failed_unused / patient_no_show
                    # / patient_canceled / office_canceled / expired / returned
                    a_status = ins_a_status if ins_a_status not in ("expired", "returned") else "failed_unused"
                else:
                    a_status = "in_progress" if assigned_date else "new"

                a = LarcAssignment(
                    device_id=device.id,
                    chart_number=patient_id,
                    patient_name=patient_name,
                    source_flow=source_flow,
                    status=a_status,
                    is_active=a_status not in ("billed", "cancelled"),
                    appt_date=appt_date,
                    inserted_at=(
                        datetime.combine(appt_date, datetime.min.time())
                        if insertion_status == "inserted" and appt_date else None
                    ),
                    billed_at=(
                        datetime.combine(billed_on, datetime.min.time())
                        if billed_on else None
                    ),
                    notes=f"Seeded from Smartsheet {args.sheet_id}",
                )
                db.add(a); db.flush()
                spawn_milestones(db, a)
                # Auto-complete milestones based on row state
                ms = {m.kind: m for m in a.milestones}
                def mark(kind, when=None):
                    m = ms.get(kind)
                    if m and m.status == "pending":
                        m.status = "done"
                        m.completed_at = (datetime.combine(when, datetime.min.time())
                                          if isinstance(when, _date) else (when or datetime.utcnow()))
                        m.completed_by = "system:smartsheet-seed"
                if assigned_date:
                    mark("benefits_verified", assigned_date)
                if signed_out_date:
                    mark("device_checked_out", signed_out_date)
                if ins_a_status == "inserted":
                    mark("device_inserted", appt_date or signed_out_date)
                if is_billed(billed):
                    mark("billed", billed_on)
                # Pharmacy-order-only milestones — assume the obvious if device was received
                if source_flow == "pharmacy_order" and arrived:
                    mark("enrollment_sent", arrived)
                    mark("enrollment_signed", arrived)
                    mark("request_faxed", arrived)
                    mark("device_received", arrived)

                log_audit(db, actor="system:smartsheet-seed", action="assignment_created",
                          device=device, assignment=a,
                          summary=(f"Seeded {dt.name} #{tag} for {patient_name} from Smartsheet"
                                   + (f" (status={a_status})" if a_status != 'new' else "")))
                n_assignments_added += 1
            else:
                # Device-only row (no patient yet)
                log_audit(db, actor="system:smartsheet-seed", action="device_added",
                          device=device,
                          summary=f"Seeded {dt.name} #{tag} from Smartsheet")

        print(f"\nSmartsheet rows: {len(rows)}")
        print(f"  Devices created:        {n_devices_added}")
        if n_tagless_synthesized:
            print(f"    (of which HIST-* synthetic: {n_tagless_synthesized})")
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
