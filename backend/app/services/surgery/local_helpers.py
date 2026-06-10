"""Helpers the surgery-create flow used to lean on Smartsheet for.

Smartsheet sync used to populate `patient_directory` and assign
`surgery_number` (SUR00xxx). As we move to upload/manual-only flows we
need both to happen inside our own code path. These helpers are
idempotent so callers (upload, manual create, merge) can call them
unconditionally.
"""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.patient_directory import PatientDirectory
from app.models.surgery import Surgery


def upsert_patient_directory(db: Session, s: Surgery) -> None:
    """Mirror the surgery's demographics into patient_directory so chart-
    number lookups (fax batches, intake search, etc.) find the patient.
    Idempotent — overwrites empty directory fields with surgery data;
    leaves existing non-empty values alone.
    """
    if not s.chart_number:
        return
    addr = " ".join(p for p in (s.address_street, s.address_city,
                                 s.address_state, s.address_zip) if p) or None
    row = (db.query(PatientDirectory)
              .filter(PatientDirectory.chart_number == s.chart_number)
              .first())
    if row is None:
        db.add(PatientDirectory(
            chart_number = s.chart_number,
            patient_name = s.patient_name,
            first_name   = s.first_name,
            last_name    = s.last_name,
            dob          = s.dob,
            gender       = s.sex,
            address      = addr,
            phone        = s.phone or s.cell_phone,
            email        = s.email,
            source_file  = "surgery_create",
            last_updated = now_utc_naive(),
        ))
        return
    # Fill in any blanks from the new surgery; never overwrite values that
    # the directory already has.
    if not row.patient_name: row.patient_name = s.patient_name
    if not row.first_name:   row.first_name   = s.first_name
    if not row.last_name:    row.last_name    = s.last_name
    if not row.dob:          row.dob          = s.dob
    if not row.gender:       row.gender       = s.sex
    if not row.address:      row.address      = addr
    if not row.phone:        row.phone        = s.phone or s.cell_phone
    if not row.email:        row.email        = s.email
    row.last_updated = now_utc_naive()


def next_surgery_number(db: Session) -> str:
    """Atomically assign the next SUR-prefixed surgery number using the
    DB sequence `surgery_number_seq`. Sequence is created and primed by
    the lightweight migration in app.database. Returns e.g. 'SUR00709'.
    """
    n = db.execute(text("SELECT nextval('surgery_number_seq')")).scalar()
    return f"SUR{int(n):05d}"


def maybe_assign_surgery_number(db: Session, s: Surgery) -> Optional[str]:
    """Assign a SUR number if the surgery doesn't already have one. Safe
    to call on rows that came from Smartsheet (they keep their existing
    number) or rows being merged onto."""
    if s.surgery_number:
        return s.surgery_number
    s.surgery_number = next_surgery_number(db)
    return s.surgery_number
