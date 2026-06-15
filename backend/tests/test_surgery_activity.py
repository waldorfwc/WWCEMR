"""SurgeryActivity model + record_activity helper (B1)."""
from datetime import date

from app.models.surgery import Surgery
from app.models.surgery_activity import SurgeryActivity
from app.services.surgery.activity import record_activity


def _surgery(db, **over):
    """A complete-info hospital surgery whose current step is `benefits`."""
    base = dict(
        chart_number="C200", patient_name="Activity, Pat",
        dob=date(1980, 1, 1), cell_phone="240-555-0200", email="a@x.c",
        address_street="1 St", address_city="Waldorf",
        address_state="MD", address_zip="20601",
        primary_insurance="Aetna", primary_member_id="M1",
        surgeon_primary="Dr. A",
        procedures=[{"cpt": "58571", "description": "TLH"}],
        diagnoses=[{"icd": "D25.9"}], estimated_minutes=180,
        eligible_facilities=["medstar"], selected_facility="medstar",
        preop_date=date(2026, 6, 1), auth_status="approved",
        status="in_progress",
        benefits_verified_at=None,
    )
    base.update(over)
    s = Surgery(**base)
    db.add(s)
    db.commit()
    return s


def test_record_activity_inserts_a_row(db):
    s = _surgery(db)
    record_activity(db, s, "date_picked",
                    "Patient picked a date: 07/01/2026 at medstar")
    db.commit()

    rows = db.query(SurgeryActivity).filter(
        SurgeryActivity.surgery_id == s.id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "date_picked"
    assert row.actor == "patient"          # default
    assert row.read_at is None
    assert "picked a date" in row.summary


def test_record_activity_soft_fails(db):
    """A bad row must not raise into the caller."""
    class Boom:
        id = "nope"

    record_activity(db, Boom(), "date_picked", "x")
