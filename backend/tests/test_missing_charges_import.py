"""Missing Charges importer dedup. Regression: a file containing the same
(patient_mrn, appointment_date) twice must not crash the whole upload on the
uq_missing_charge_mrn_date unique constraint (the session is autoflush=False,
so the in-loop DB existence check can't see the first pending insert)."""
from datetime import date

from app.models.missing_charge import MissingCharge, MissingChargeImport
from app.services.missing_charges_import import import_rows


def _row(mrn, dos, name="Doe, J"):
    return {
        "patient_mrn": mrn, "appointment_date": dos, "patient_name": name,
        "patient_dob": date(1980, 1, 1), "appointment_type": "WWE - Est",
        "appointment_status": "Checked Out", "visit_status": "Preliminary",
        "payer": "BCBS", "primary_provider": "Salley, Danielle",
        "bill_same_dos": "No", "bill_same_dos_loc": "No",
        "appointment_count": 1, "patient_link": "https://wwc.ema.md/x",
    }


def _import(db):
    imp = MissingChargeImport(original_filename="f.xlsx", uploaded_by="t@x", total_rows=0)
    db.add(imp); db.flush()
    return imp


def test_in_file_duplicate_mrn_date_does_not_crash(db):
    imp = _import(db)
    rows = [_row("48804", date(2026, 5, 19), "Smith, A"),
            _row("48804", date(2026, 5, 19), "Smith, A")]
    new, dup, err = import_rows(db, rows, import_id=imp.id)
    db.commit()                       # must NOT raise UniqueViolation
    assert (new, dup, err) == (1, 1, 0)
    assert db.query(MissingCharge).filter(
        MissingCharge.patient_mrn == "48804").count() == 1


def test_dedupes_against_existing_db_row(db):
    imp = _import(db)
    import_rows(db, [_row("100", date(2026, 5, 1))], import_id=imp.id)
    db.commit()
    n2, d2, e2 = import_rows(db, [_row("100", date(2026, 5, 1))], import_id=imp.id)
    db.commit()
    assert (n2, d2, e2) == (0, 1, 0)
    assert db.query(MissingCharge).count() == 1


def test_missing_key_counts_as_error(db):
    imp = _import(db)
    new, dup, err = import_rows(
        db, [_row(None, date(2026, 5, 1)), _row("200", None)], import_id=imp.id)
    db.commit()
    assert (new, dup, err) == (0, 0, 2)
