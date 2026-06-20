from datetime import date
from app.models.larc import LarcAssignment, LarcDeviceType
from app.services.larc import portal_auth


def _a(db, dob=date(1990, 5, 1), cell="240-555-0123"):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="P1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="pharmacy_order", status="new", is_active=True,
                       patient_dob=dob, patient_cell=cell, portal_token_version=0)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_match_assignment_by_dob_last4(db):
    a = _a(db)
    got = portal_auth.match_assignment(db, date(1990, 5, 1), "0123")
    assert got is not None and got.id == a.id


def test_match_returns_none_for_wrong_last4(db):
    _a(db)
    assert portal_auth.match_assignment(db, date(1990, 5, 1), "9999") is None


def test_token_roundtrip(db):
    a = _a(db)
    tok = portal_auth.issue_portal_token(a)
    payload = portal_auth.decode_portal_token(tok)
    assert payload["scope"] == "larc_portal"
    assert payload["sub"] == str(a.id)
    assert payload["lpv"] == 0
