"""Excel parser for the ModMed 'Appointment Missing Charges' report.

Idempotent on (patient_mrn, appointment_date) — re-uploading the same
file produces zero new rows. Caller wraps in a transaction.
"""
from __future__ import annotations

import io
from datetime import date as _date, datetime
from typing import Iterable, Optional, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from app.models.missing_charge import MissingCharge, MissingChargeImport


# Map ModMed Excel header → our model attribute. Headers we expect from
# the report's first sheet (see Sheet1 columns).
HEADER_MAP = {
    "Appointment Date":                          "appointment_date",
    "Patient MRN":                               "patient_mrn",
    "Patient Name":                              "patient_name",
    "Patient DOB":                               "patient_dob",
    "Appointment Type":                          "appointment_type",
    "Appointment Status":                        "appointment_status",
    "Bill With Same Service Date & Location?":   "bill_same_dos_loc",
    "Bill With Same Service Date?":              "bill_same_dos",
    "Payer":                                     "payer",
    "Primary Provider":                          "primary_provider",
    "Visit Status":                              "visit_status",
    "Patient Link":                              "patient_link",
    "Appointment Count":                          "appointment_count",
}


def _to_date(v) -> Optional[_date]:
    if v is None or pd.isna(v):
        return None
    if isinstance(v, _date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None


def _to_str(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return str(v).strip() or None


def _to_int(v) -> Optional[int]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_excel(file_bytes: bytes) -> list[dict]:
    """Read the Excel and yield row-dicts already mapped to model fields."""
    bio = io.BytesIO(file_bytes)
    df = pd.read_excel(bio)
    missing_required = {"Patient MRN", "Appointment Date"} - set(df.columns)
    if missing_required:
        raise ValueError(f"Missing required columns: {sorted(missing_required)}")

    rows: list[dict] = []
    for _, r in df.iterrows():
        row = {
            "appointment_date":    _to_date(r.get("Appointment Date")),
            "patient_mrn":         _to_str(r.get("Patient MRN")),
            "patient_name":        _to_str(r.get("Patient Name")),
            "patient_dob":         _to_date(r.get("Patient DOB")),
            "appointment_type":    _to_str(r.get("Appointment Type")),
            "appointment_status":  _to_str(r.get("Appointment Status")),
            "visit_status":        _to_str(r.get("Visit Status")),
            "payer":               _to_str(r.get("Payer")),
            "primary_provider":    _to_str(r.get("Primary Provider")),
            "bill_same_dos":       _to_str(r.get("Bill With Same Service Date?")),
            "bill_same_dos_loc":   _to_str(r.get("Bill With Same Service Date & Location?")),
            "appointment_count":   _to_int(r.get("Appointment Count")),
            "patient_link":        _to_str(r.get("Patient Link")),
        }
        rows.append(row)
    return rows


def import_rows(
    db: Session, rows: Iterable[dict], *,
    import_id: str,
    initial_status: str = "new",
) -> Tuple[int, int, int]:
    """Upsert by (patient_mrn, appointment_date). Returns
    (new_count, duplicate_count, error_count)."""
    new_count = dup_count = err_count = 0

    for row in rows:
        mrn = row.get("patient_mrn")
        dos = row.get("appointment_date")
        if not mrn or not dos:
            err_count += 1
            continue

        existing = (db.query(MissingCharge)
                      .filter(MissingCharge.patient_mrn == mrn,
                              MissingCharge.appointment_date == dos)
                      .first())
        if existing:
            dup_count += 1
            # Keep workflow status untouched; refresh snapshot fields the
            # provider/scheduler might have updated on the source row.
            for k in ("patient_name", "appointment_type", "appointment_status",
                      "visit_status", "payer", "primary_provider",
                      "bill_same_dos", "bill_same_dos_loc", "patient_link"):
                v = row.get(k)
                if v and not getattr(existing, k):
                    setattr(existing, k, v)
            continue

        mc = MissingCharge(
            source_import_id=import_id,
            status=initial_status,
            **{k: v for k, v in row.items() if v is not None},
        )
        db.add(mc)
        new_count += 1

    return new_count, dup_count, err_count
