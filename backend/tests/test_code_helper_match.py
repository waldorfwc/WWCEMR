"""Unit tests for patient roster matching helper."""
from datetime import date
import pytest

from app.models.patient import Patient
from app.services.code_helper_match import match_patient, MatchKind


def _seed(db, *patients):
    for p in patients:
        db.add(p)
    db.commit()


def test_match_patient_exact_one(db):
    _seed(db,
        Patient(patient_id="P001", first_name="Jane",  last_name="Smith",
                 date_of_birth=date(1985, 3, 12)),
        Patient(patient_id="P002", first_name="Other", last_name="Jones",
                 date_of_birth=date(1985, 3, 12)),
    )
    r = match_patient(db, name="Jane Smith", dob=date(1985, 3, 12))
    assert r.kind == MatchKind.ONE
    assert r.patient_id == "P001"


def test_match_patient_no_match(db):
    _seed(db, Patient(patient_id="P001", first_name="Jane",
                       last_name="Smith", date_of_birth=date(1985, 3, 12)))
    r = match_patient(db, name="Nobody Here", dob=date(1990, 1, 1))
    assert r.kind == MatchKind.NONE
    assert r.patient_id is None


def test_match_patient_ambiguous(db):
    _seed(db,
        Patient(patient_id="P001", first_name="Jane",  last_name="Smith",
                 date_of_birth=date(1985, 3, 12)),
        Patient(patient_id="P002", first_name="Janet", last_name="Smith",
                 date_of_birth=date(1985, 3, 12)),
    )
    r = match_patient(db, name="Smith", dob=date(1985, 3, 12))
    assert r.kind == MatchKind.AMBIGUOUS
    assert r.patient_id is None
    assert set(r.candidates) == {"P001", "P002"}


def test_match_patient_lastname_only_works(db):
    _seed(db, Patient(patient_id="P001", first_name="Jane",
                       last_name="Smith", date_of_birth=date(1985, 3, 12)))
    r = match_patient(db, name="Smith", dob=date(1985, 3, 12))
    assert r.kind == MatchKind.ONE


def test_match_patient_none_when_no_dob(db):
    _seed(db, Patient(patient_id="P001", first_name="Jane",
                       last_name="Smith", date_of_birth=date(1985, 3, 12)))
    r = match_patient(db, name="Jane Smith", dob=None)
    assert r.kind == MatchKind.NONE
