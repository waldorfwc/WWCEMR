"""Backfill PelletVisit + PelletVisitDose rows from the two Smartsheet
pellet trackers (White Plains + Brandywine).

Why this exists: pellet_smartsheet_seed.py reads the same sheets but
only computes current-stock balance. It throws away the per-row
Date / Chart # / Last Name / Provider / Pellet Visit ID / Lot # /
quantity, which is the actual chain-of-custody history for Schedule
III testosterone.

This script reads every row that's an 'Out' event for a real patient
(Chart # != '00000' and not 'DROPPED') and creates:
  - PelletVisit (one per distinct Smartsheet row, status='inserted',
    is_historical=True). Idempotent via smartsheet_row_id.
  - PelletVisitDose (one per dose column checked on that row,
    status='inserted', linked to the lot by qualgen_lot_number).

Out events with chart == '00000' or last name 'DROPPED' become
PelletDisposal entries instead (broken/lost on the floor).

In events are SKIPPED — they're inventory receipts, already handled
by pellet_smartsheet_seed.py.

Dry-run by default. Pass --apply to commit.

Usage:
  cd backend
  ./venv/bin/python scripts/pellet_smartsheet_history_import.py            # dry-run
  ./venv/bin/python scripts/pellet_smartsheet_history_import.py --apply    # commit
  ./venv/bin/python scripts/pellet_smartsheet_history_import.py --location=white_plains
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
    PelletAuditEvent, PelletDisposal, PelletDoseType, PelletLot,
    PelletPatient, PelletStock, PelletTransfer, PelletVisit,
    PelletVisitDose,
)

SHEET_LOCATIONS = [
    ("5786899659941764", "white_plains"),
    ("2024076226809732", "brandywine"),
]

DOSE_COLUMNS = ['6mg', '10mg', '12.5mg', '15mg', '18mg', '20mg',
                '25mg', '37.5mg', '50mg', '87.5mg', '100mg', '200mg']

ESTRADIOL_DOSES = {6.0, 10.0, 12.5, 15.0, 18.0, 20.0}
TESTOSTERONE_DOSES = {25.0, 37.5, 50.0, 87.5, 100.0, 200.0}

UNKNOWN_EXP = _date(2099, 12, 31)
ACTOR = "system:smartsheet-history"


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


def fetch_sheet(token: str, sheet_id: str) -> dict:
    # Smartsheet paginates large sheets — pull everything in one go via
    # the 'objectIds' / pageSize=10000 trick (max page size is 10000).
    r = requests.get(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}?pageSize=10000",
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def parse_dose(s: str) -> Optional[float]:
    if not s: return None
    try:
        return float(s.replace('mg', ''))
    except ValueError:
        return None


def infer_hormone(raw_type: str, dose_mg: float) -> Optional[str]:
    """Smartsheet's 'Pellet Type' column was sometimes filled in wrong
    (e.g. 'Estrogen' on a row with a 100mg dose, but 100mg only exists
    as testosterone in this practice's inventory). The dose strengths
    DO NOT overlap between hormones — so dose is authoritative. Label
    is used only as a fallback for non-standard doses we don't recognize."""
    if dose_mg in ESTRADIOL_DOSES:    return "estradiol"
    if dose_mg in TESTOSTERONE_DOSES: return "testosterone"
    t = (raw_type or "").lower()
    if "estro" in t or "estradiol" in t: return "estradiol"
    if "testosterone" in t:              return "testosterone"
    return None


def safeint(v) -> int:
    if v in (None, ''): return 0
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return 0


def parse_date(v) -> Optional[_date]:
    if not v:
        return None
    s = str(v).strip()
    # Smartsheet date cells come back as 'YYYY-MM-DD'
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def classify_non_patient_row(chart: str, last_name: str, provider: str,
                              notes: str) -> Optional[str]:
    """Smartsheet uses chart='00000' for ALL non-patient events. The
    actual event type lives in (Last Name, Notes) — Provider is just
    a placeholder ('DROPPED') and is NOT literal.

    Common Last Name patterns observed in production data:
      DROPPED / DROPPED PELLET / EXPIRED / LOST / BROKEN PELLET → disposal
      ORDER OUT / ORDER TO BW / SENT TO BW / TO BRANDYWINE /
        PELLETS TO BW / TRANSFERRED / BRANDYWINE TRAN / TO BW /
        DR MUSSENDEN / DR COOKE TO TEXAS                       → transfer
      ORDER IN / ORDER / PELLET IN                             → inventory_in
      BACK UP BAG / BACKUP BAG                                 → backup_bag
      CORRECTION                                               → correction

    Returns one of those strings, or None for a real patient row.
    """
    c = (chart or "").strip().lstrip("0")
    if c != "":
        return None  # patient row

    blob = f"{(last_name or '').strip().upper()} {(notes or '').strip().upper()}"

    # Transfer wins over inventory_in when both apply (e.g. 'ORDER OUT' +
    # 'TO BW' is a transfer, not an inventory_in).
    TRANSFER_KEYS = (
        "TO BW", "TO ARL", "TO BRANDYWINE", "TO TEXAS",
        "SENT TO", "TRANSFER", "GIVEN TO", "TAKE TO",
        "DR MUSSENDEN", "DR COOKE",
        "ORDER OUT", "ORDER TO ", "PELLETS TO ",
    )
    DISPOSAL_KEYS = ("DROPPED", "EXPIRED", "LOST", "BROKEN")
    INVENTORY_IN_KEYS = ("ORDER IN", "PELLET IN", "ORDER  IN")
    BACKUP_KEYS = ("BACK UP BAG", "BACKUP BAG")
    CORRECTION_KEYS = ("CORRECTION",)

    if any(k in blob for k in TRANSFER_KEYS):
        return "transfer"
    if any(k in blob for k in DISPOSAL_KEYS):
        return "disposal"
    if any(k in blob for k in INVENTORY_IN_KEYS):
        return "inventory_in"
    if any(k in blob for k in BACKUP_KEYS):
        return "backup_bag"
    if any(k in blob for k in CORRECTION_KEYS):
        return "correction"
    # 'ORDER' alone (no IN/OUT/TO) — assume inventory_in; admins logged
    # this when an order arrived without bothering to write 'IN'.
    if "ORDER" in blob:
        return "inventory_in"
    # 'BRANDYWINE' alone with no other keyword — most likely a transfer
    # destination shorthand. Better than calling it a disposal.
    if "BRANDYWINE" in blob or " BW " in blob:
        return "transfer"
    return "skip"


_TRANSFER_DEST_RX = [
    ("BW",                ["BW", "BRANDYWINE"]),
    ("arlington",         ["ARL", "ARLINGTON"]),
    ("dr_cooke_external", ["COOKE", "TEXAS", "MUSSENDEN"]),
]
def infer_transfer_destination(last_name: str, notes: str, source_location: str) -> str:
    blob = f"{(last_name or '').upper()} {(notes or '').upper()}"
    for code, keywords in _TRANSFER_DEST_RX:
        if any(k in blob for k in keywords):
            # BW means "to brandywine" from white_plains; from brandywine,
            # we don't really know — but flag as 'brandywine' anyway.
            return {"BW": "brandywine"}.get(code, code)
    return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Commit changes (default: dry-run)")
    ap.add_argument("--location", choices=["white_plains", "brandywine"],
                    help="Limit to one sheet")
    ap.add_argument("--limit-rows", type=int, default=0,
                    help="Stop after this many rows (per sheet). 0 = no limit.")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        token = get_token()

        # Catalogs
        dose_by_key = {(t.hormone, float(t.dose_mg)): t
                       for t in db.query(PelletDoseType).all()}
        lots_by_number = {(l.qualgen_lot_number.strip(), str(l.dose_type_id), l.location): l
                          for l in db.query(PelletLot).all()
                          if l.qualgen_lot_number and l.location}
        patients_by_chart = {(p.chart_number or "").strip().lstrip("0"): p
                             for p in db.query(PelletPatient).all()}
        already_imported = {v.smartsheet_row_id
                            for v in db.query(PelletVisit)
                                       .filter(PelletVisit.smartsheet_row_id.isnot(None))
                                       .all()}

        stats = defaultdict(int)
        skip_reasons = defaultdict(int)
        unknown_charts = defaultdict(int)        # chart -> count
        unknown_chart_examples = {}              # chart -> (date, last_name)
        unknown_dose_types = defaultdict(int)    # (hormone, dose_mg) -> count

        # Cache (patient_id, scheduled_date) → PelletVisit within this run.
        # Multiple Smartsheet rows for the same patient/date collapse into
        # one visit with multiple PelletVisitDose children.
        visit_cache: dict = {}

        for sheet_id, location in SHEET_LOCATIONS:
            if args.location and args.location != location:
                continue
            print(f"\n══ {location} (sheet {sheet_id}) ══")
            sheet = fetch_sheet(token, sheet_id)
            col_by_id = {c["id"]: c["title"] for c in sheet["columns"]}
            rows = sheet.get("rows", [])
            print(f"  {len(rows)} rows total")

            for n, row in enumerate(rows, 1):
                if args.limit_rows and n > args.limit_rows:
                    break

                row_uid = f"{sheet_id}:{row['id']}"
                if row_uid in already_imported:
                    stats["skipped_already_imported"] += 1
                    continue

                f = {}
                for cell in row.get("cells", []):
                    t = col_by_id.get(cell.get("columnId"))
                    v = cell.get("displayValue") or cell.get("value")
                    if v not in (None, ''):
                        f[t] = v

                date_v = parse_date(f.get("Date"))
                chart  = (f.get("Chart #") or "").strip()
                last_n = (f.get("Last Name") or "").strip()
                provider = (f.get("Provider") or "").strip()
                visit_id_ss = (f.get("Pellet Visit ID") or "").strip()
                initials = (f.get("Initials") or "").strip()
                notes  = (f.get("Notes") or "").strip()
                raw_type = (f.get("Pellet Type") or "").strip()

                # Walk each dose column on the row. A row can carry
                # multiple doses (the typical patient bag).
                row_doses = []
                for dose_col in DOSE_COLUMNS:
                    if not f.get(dose_col):
                        continue
                    qty = safeint(f.get(f"{dose_col} Quantity", 0))
                    if qty == 0:
                        continue
                    io = (f.get(f"{dose_col} In/Out") or "").strip()
                    if io != "Out":
                        # In rows = inventory receipts, already handled
                        # by the seed script. Skip.
                        continue
                    lot_raw = str(f.get(f"Lot # {dose_col}", "") or "").strip()
                    dose_mg = parse_dose(dose_col)
                    if dose_mg is None:
                        skip_reasons["bad_dose"] += 1
                        continue
                    hormone = infer_hormone(raw_type, dose_mg)
                    if not hormone:
                        skip_reasons["unknown_hormone"] += 1
                        continue
                    dose_type = dose_by_key.get((hormone, dose_mg))
                    if not dose_type:
                        # Auto-create the missing catalog entry — only happens
                        # for real-but-uncatalogued dose strengths (e.g.,
                        # estradiol 18mg). Marked is_active=False so it
                        # doesn't pollute the active picker; admin can flip
                        # active in the UI if it's a current product.
                        dose_type = PelletDoseType(
                            hormone=hormone,
                            dose_mg=Decimal(str(dose_mg)),
                            label=f"{hormone.title()} {dose_mg:g}mg",
                            is_controlled=(hormone == "testosterone"),
                            is_active=False,
                            notes=f"Auto-created from Smartsheet history import (was used "
                                  f"in past visits but not in the active catalog).",
                        )
                        db.add(dose_type); db.flush()
                        dose_by_key[(hormone, dose_mg)] = dose_type
                        stats["dose_types_auto_created"] += 1
                        unknown_dose_types[(hormone, dose_mg)] += 1
                    row_doses.append({
                        "dose_col": dose_col, "dose_mg": dose_mg,
                        "hormone": hormone, "qty": qty,
                        "lot_raw": lot_raw, "dose_type": dose_type,
                    })

                if not row_doses:
                    continue  # In-row or no-dose row, silently skip

                # Classify non-patient rows by Notes/LastName, not just
                # by Provider='DROPPED' (which is a placeholder sentinel,
                # not the literal event type).
                row_kind = classify_non_patient_row(chart, last_n, provider, notes)
                if row_kind is not None:
                    stats[f"row_kind_{row_kind}"] += 1
                    if row_kind == "inventory_in":
                        # Already handled by pellet_smartsheet_seed.py
                        continue
                    if row_kind in ("backup_bag", "skip"):
                        continue
                    if not args.apply:
                        labels = ", ".join(f"{rd['qty']}x{rd['dose_col']} ({rd['lot_raw'] or '?'})"
                                            for rd in row_doses)
                        print(f"  [{n}] {date_v} {row_kind.upper():10s} "
                              f"last={last_n!r} notes={notes!r:30s} → {labels}")
                        continue
                    # Commit path for disposals + transfers
                    for rd in row_doses:
                        if not rd["lot_raw"]:
                            skip_reasons[f"{row_kind}_no_lot"] += 1
                            continue
                        lot = lots_by_number.get((rd["lot_raw"], str(rd["dose_type"].id), location))
                        if not lot:
                            lot = PelletLot(
                                qualgen_lot_number=rd["lot_raw"],
                                dose_type_id=rd["dose_type"].id,
                                location=location,
                                doses_originally_received=rd["qty"],
                                expiration_date=UNKNOWN_EXP,
                                received_at=datetime.combine(
                                    date_v or _date.today(), datetime.min.time()),
                                received_by=ACTOR,
                                notes="Auto-created from Smartsheet history import",
                            )
                            db.add(lot); db.flush()
                            lots_by_number[(rd["lot_raw"], str(rd["dose_type"].id), location)] = lot
                            stats["lots_auto_created"] += 1
                        if row_kind == "disposal":
                            db.add(PelletDisposal(
                                lot_id=lot.id,
                                location=location,
                                doses=rd["qty"],
                                reason=("expired" if "EXPIR" in (notes + last_n).upper()
                                         else "dropped"),
                                occurred_at=datetime.combine(
                                    date_v or _date.today(), datetime.min.time()),
                                performed_by=initials or ACTOR,
                                notes=(f"Smartsheet row {row_uid}. {last_n} {notes}").strip(),
                            ))
                            stats["disposals_created"] += 1
                        elif row_kind == "transfer":
                            dest = infer_transfer_destination(last_n, notes, location)
                            db.add(PelletTransfer(
                                lot_id=lot.id,
                                from_location=location,
                                to_location=dest,
                                doses=rd["qty"],
                                sent_at=datetime.combine(
                                    date_v or _date.today(), datetime.min.time()),
                                sent_by=initials or ACTOR,
                                status="received",  # historical — assume completed
                                notes=(f"Smartsheet row {row_uid}. {last_n} {notes}").strip(),
                            ))
                            stats["transfers_created"] += 1
                    continue

                # Patient allocation path — need a PelletPatient. Auto-create
                # a stub for any chart we don't recognize, so the historical
                # audit trail isn't lost. Admin can fill in DOB / email /
                # phone later if/when the patient comes back.
                chart_norm = chart.lstrip("0") or chart
                patient = patients_by_chart.get(chart_norm)
                if not patient:
                    unknown_charts[chart] += 1
                    unknown_chart_examples.setdefault(chart, (date_v, last_n))
                    patient = PelletPatient(
                        chart_number=chart,
                        patient_name=last_n or f"(unknown — chart {chart})",
                        patient_type="established",
                        status="inactive",
                        mammo_verified=False,
                        labs_verified=False,
                        notes=(f"Auto-created from Smartsheet history import. "
                               f"First seen: {date_v} ({last_n}). "
                               f"Fill in DOB/email/phone if patient is still active."),
                        created_by=ACTOR,
                    )
                    db.add(patient); db.flush()
                    patients_by_chart[chart_norm] = patient
                    stats["patients_auto_created"] += 1
                    if not args.apply:
                        print(f"  [{n}] {date_v} chart={chart} ({last_n}) — would auto-create PelletPatient stub")

                # Group rows by (patient, date) into one visit with N doses.
                # Multiple Smartsheet rows on the same date represent one
                # physical insertion event with multiple pellets.
                visit_key = (patient.id, date_v)
                existing_visit = visit_cache.get(visit_key)

                if not args.apply:
                    if existing_visit is None:
                        # mark seen so we count visits correctly in dry-run
                        visit_cache[visit_key] = "would-create"
                        stats["visits_would_create"] += 1
                    print(f"  [{n}] {date_v} chart={chart} {last_n} → "
                          + ("APPEND " if existing_visit else "NEW    ")
                          + f"{len(row_doses)} dose(s): "
                          + ", ".join(f"{rd['qty']}x{rd['dose_col']} ({rd['lot_raw'] or '?'})"
                                       for rd in row_doses))
                    stats["doses_would_create"] += len(row_doses)
                    continue

                # Commit path — find existing OR create
                visit = existing_visit
                if visit is None:
                    # Match an existing PelletVisit at the same date for this
                    # patient (e.g., a ModMed-imported appointment) and attach
                    # doses to it. Otherwise create a fresh historical visit.
                    if date_v:
                        visit = (db.query(PelletVisit)
                                   .filter(PelletVisit.patient_id == patient.id,
                                           PelletVisit.scheduled_date == date_v)
                                   .first())
                        if visit is not None:
                            stats["visits_attached_to_existing"] += 1
                            if not visit.smartsheet_row_id:
                                visit.smartsheet_row_id = row_uid
                    if visit is None:
                        visit = PelletVisit(
                            patient_id=patient.id,
                            visit_kind="initial",
                            status="inserted",
                            is_historical=True,
                            scheduled_date=date_v,
                            location=location,
                            # provider = the doctor who did the procedure
                            provider=provider or None,
                            inserted_at=datetime.combine(
                                date_v or _date.today(), datetime.min.time()),
                            # inserted_by = doctor (smartsheet doesn't track
                            # a separate "inserter" beyond Provider)
                            inserted_by=provider or ACTOR,
                            # bagged_by = MA who entered the row (Initials col).
                            # In WWC's workflow the MA is also the one who
                            # pulled+bagged the pellets, so this is accurate.
                            bagged_by=initials or None,
                            outcome="inserted",
                            notes=(f"Smartsheet history import. {last_n} {notes}").strip(),
                            smartsheet_row_id=row_uid,
                            smartsheet_visit_id=visit_id_ss or None,
                            created_by=ACTOR,
                        )
                        db.add(visit); db.flush()
                        stats["visits_created"] += 1
                    visit_cache[visit_key] = visit

                base_pos = (db.query(PelletVisitDose)
                              .filter(PelletVisitDose.visit_id == visit.id)
                              .count())

                for pos, rd in enumerate(row_doses, start=base_pos):
                    lot = lots_by_number.get((rd["lot_raw"], str(rd["dose_type"].id), location)) if rd["lot_raw"] else None
                    if not lot and rd["lot_raw"]:
                        # New lot referenced in history that's not in PelletLot
                        # yet — create with placeholder exp.
                        lot = PelletLot(
                            qualgen_lot_number=rd["lot_raw"],
                            dose_type_id=rd["dose_type"].id,
                            location=location,
                            doses_originally_received=rd["qty"],
                            expiration_date=UNKNOWN_EXP,
                            received_at=datetime.combine(date_v or _date.today(), datetime.min.time()),
                            received_by=ACTOR,
                            notes="Auto-created from Smartsheet history import",
                        )
                        db.add(lot); db.flush()
                        lots_by_number[(rd["lot_raw"], str(rd["dose_type"].id), location)] = lot
                        stats["lots_auto_created"] += 1

                    db.add(PelletVisitDose(
                        visit_id=visit.id,
                        dose_type_id=rd["dose_type"].id,
                        lot_id=lot.id if lot else None,
                        quantity=rd["qty"],
                        position=pos,
                        status="inserted",
                        pulled_at=visit.inserted_at,
                        pulled_by=visit.inserted_by,
                        resolved_at=visit.inserted_at,
                        resolved_by=visit.inserted_by,
                        notes=f"Smartsheet row {row_uid}",
                    ))
                stats["doses_created"] += len(row_doses)

                if stats["doses_created"] % 200 == 0:
                    db.commit()
                    print(f"    committed (visits={stats['visits_created']}, "
                          f"attached={stats['visits_attached_to_existing']}, "
                          f"doses={stats['doses_created']})")

        if args.apply:
            db.commit()
        else:
            db.rollback()

        print()
        print("══════ summary ══════")
        for k, v in sorted(stats.items()):
            print(f"  {k:35s} {v:>8d}")
        if skip_reasons:
            print("  skip reasons:")
            for k, v in sorted(skip_reasons.items()):
                print(f"    {k:35s} {v:>8d}")
        if unknown_charts:
            print()
            print(f"  Unknown chart #s ({len(unknown_charts)} distinct):")
            for chart, cnt in sorted(unknown_charts.items(), key=lambda x: -x[1]):
                ex_date, ex_last = unknown_chart_examples[chart]
                print(f"    {chart:>10s} ({cnt:>3}x)  e.g. {ex_date} {ex_last!r}")
        if unknown_dose_types:
            print()
            print("  Dose-type combinations not in PelletDoseType catalog:")
            for (hormone, dose_mg), cnt in sorted(unknown_dose_types.items(), key=lambda x: -x[1]):
                print(f"    {hormone:>12s} {dose_mg}mg  ({cnt:>3}x)")
        if not args.apply:
            print()
            print("  (dry-run — no rows committed. Re-run with --apply to commit.)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
