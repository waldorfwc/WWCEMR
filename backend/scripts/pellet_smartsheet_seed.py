"""Seed Pellet opening balances from the two Smartsheet trackers.

Sheets (sheet_id → location):
  5786899659941764  White Plains
  2024076226809732  Brandywine

Each sheet is a transaction log (one row per pellet event). We compute
the current per-(hormone, dose_mg, lot) balance from sum(In) − sum(Out),
then create one PelletLot + PelletStock row per positive-balance lot.

Lots are imported with expiration_date=2099-12-31 (placeholder) and a
notes flag — admin updates from the physical box.

Hormone normalization:
  Pellet Type='Estrogen' or 'Estradiol' → estradiol
  Pellet Type='Testosterone'            → testosterone
  Pellet Type='ORDER IN'                → infer from dose strength
                                          (≤20mg=estradiol, ≥25mg=testosterone)

Run with no flag = dry-run. Pass --apply to commit.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date as _date, datetime
from decimal import Decimal
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

from app.database import SessionLocal, init_db
from app.models.pellet import (
    PelletAuditEvent, PelletDoseType, PelletLot, PelletReceipt, PelletStock,
)


SHEET_LOCATIONS = [
    ("5786899659941764", "white_plains"),
    ("2024076226809732", "brandywine"),
]

DOSE_COLUMNS = ['6mg', '10mg', '12.5mg', '15mg', '18mg', '20mg',
                '25mg', '37.5mg', '50mg', '87.5mg', '100mg', '200mg']

# Inferred hormone for 'ORDER IN' rows based on dose strength
ESTRADIOL_DOSES = {6.0, 10.0, 12.5, 15.0, 18.0, 20.0}
TESTOSTERONE_DOSES = {25.0, 37.5, 50.0, 87.5, 100.0, 200.0}

UNKNOWN_EXP = _date(2099, 12, 31)


def safeint(v) -> int:
    if v in (None, ''): return 0
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return 0


def parse_dose(s: str) -> Optional[float]:
    if not s: return None
    try:
        return float(s.replace('mg', ''))
    except ValueError:
        return None


def infer_hormone(raw_type: str, dose_mg: float) -> Optional[str]:
    t = (raw_type or '').strip().lower()
    if t in ('estrogen', 'estradiol'):
        return 'estradiol'
    if t == 'testosterone':
        return 'testosterone'
    if t == 'order in':
        if dose_mg in ESTRADIOL_DOSES:
            return 'estradiol'
        if dose_mg in TESTOSTERONE_DOSES:
            return 'testosterone'
    return None


def fetch_sheet(token: str, sheet_id: str) -> dict:
    r = requests.get(f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


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


def compute_balances(sheet: dict) -> tuple[dict, dict]:
    """Return ({(hormone, dose_mg, lot): doses}, {warnings_counter}).
    Only positive balances are kept."""
    col_by_id = {c["id"]: c["title"] for c in sheet["columns"]}
    bal: dict = defaultdict(int)
    warnings: dict = defaultdict(int)

    for row in sheet.get("rows", []):
        f = {}
        for cell in row.get("cells", []):
            t = col_by_id.get(cell.get("columnId"))
            v = cell.get("displayValue") or cell.get("value")
            if v not in (None, ''):
                f[t] = v
        raw_type = (f.get("Pellet Type") or '').strip()

        for dose_col in DOSE_COLUMNS:
            if not f.get(dose_col):    # checkbox False / unchecked
                continue
            qty = safeint(f.get(f"{dose_col} Quantity", 0))
            if qty == 0:
                continue
            io = f.get(f"{dose_col} In/Out")
            lot_raw = f.get(f"Lot # {dose_col}", '')
            lot = (str(lot_raw or '').strip() or '(no-lot)')

            dose_mg = parse_dose(dose_col)
            if dose_mg is None:
                warnings['bad_dose'] += 1
                continue
            hormone = infer_hormone(raw_type, dose_mg)
            if not hormone:
                warnings['unknown_hormone'] += 1
                continue

            key = (hormone, dose_mg, lot)
            if io == "In":
                bal[key] += qty
            elif io == "Out":
                bal[key] -= qty

    positive = {k: v for k, v in bal.items() if v > 0}
    return positive, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        token = get_token()

        # Load the dose-type catalog
        catalog = {(t.hormone, float(t.dose_mg)): t
                    for t in db.query(PelletDoseType).all()}

        total_lots_created = 0
        total_doses_added = 0
        skipped_no_catalog = 0
        skipped_no_lot = 0
        skipped_already_exists = 0

        for sheet_id, location in SHEET_LOCATIONS:
            print(f"\n══ Sheet {sheet_id} → {location} ══")
            sheet = fetch_sheet(token, sheet_id)
            balances, warns = compute_balances(sheet)
            print(f"  Sheet: {sheet['name']!r}")
            print(f"  Positive-balance lots: {len(balances)}")
            print(f"  Total doses to import: {sum(balances.values())}")
            if warns:
                print(f"  Warnings: {dict(warns)}")

            # Create one opening-balance receipt for this location
            r = PelletReceipt(
                qualgen_order_number=f"OPENING-BALANCE-{location.upper()}",
                received_date=_date.today(),
                received_by="system:smartsheet-seed",
                location=location,
                manifest_verified=True,
                manifest_verified_by="system:smartsheet-seed",
                manifest_verified_at=datetime.utcnow(),
                notes=(f"Opening balance imported from Smartsheet "
                       f"{sheet_id} ({sheet['name']!r}) on "
                       f"{_date.today().isoformat()}. Expiration dates "
                       f"set to 2099-12-31 — admin to update from "
                       f"physical inventory."),
            )
            db.add(r); db.flush()

            for (hormone, dose_mg, lot_str), doses in sorted(balances.items()):
                if lot_str == '(no-lot)':
                    skipped_no_lot += 1
                    continue
                dt = catalog.get((hormone, dose_mg))
                if not dt:
                    skipped_no_catalog += 1
                    print(f"    ! No catalog entry for ({hormone}, {dose_mg}mg) — skipping {doses} doses lot {lot_str}")
                    continue

                # Skip if a lot with the same (dose_type, qualgen_lot) already exists
                # AT THIS LOCATION (allows same lot # to be opened at two locations
                # via separate receipts, e.g. White Plains AND Brandywine)
                existing = (db.query(PelletLot)
                              .join(PelletStock,
                                    PelletStock.lot_id == PelletLot.id)
                              .filter(PelletLot.dose_type_id == dt.id,
                                      PelletLot.qualgen_lot_number == lot_str,
                                      PelletStock.location == location)
                              .first())
                if existing:
                    skipped_already_exists += 1
                    continue

                lot = PelletLot(
                    dose_type_id=dt.id,
                    qualgen_lot_number=lot_str,
                    expiration_date=UNKNOWN_EXP,
                    doses_originally_received=doses,
                    receipt_id=r.id,
                    received_by="system:smartsheet-seed",
                    notes=("Expiration unknown — Smartsheet import. "
                           "Admin: verify physical pack + update exp."),
                )
                db.add(lot); db.flush()

                stock = PelletStock(
                    lot_id=lot.id,
                    location=location,
                    doses_on_hand=doses,
                )
                db.add(stock)

                db.add(PelletAuditEvent(
                    actor="system:smartsheet-seed",
                    action="opening_balance",
                    dose_type_id=dt.id,
                    lot_id=lot.id,
                    receipt_id=r.id,
                    location=location,
                    delta_doses=doses,
                    summary=(f"Opening balance: {doses} {dt.label} "
                             f"lot {lot_str} → {location}"),
                    detail={"sheet_id": sheet_id, "source_hormone_label": hormone},
                ))
                total_lots_created += 1
                total_doses_added += doses

        print(f"\n── Summary ──")
        print(f"  Lots created:                 {total_lots_created}")
        print(f"  Doses added to inventory:     {total_doses_added}")
        print(f"  Skipped — no catalog match:   {skipped_no_catalog}")
        print(f"  Skipped — no lot # in source: {skipped_no_lot}")
        print(f"  Skipped — already in DB:      {skipped_already_exists}")

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
