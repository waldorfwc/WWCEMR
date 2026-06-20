"""Start-LARC-Process create-assignment behavior: reason + provider capture."""
from app.models.larc import LarcAssignment, LarcDeviceType


def _dt(db, name="Mirena", default_flow="pharmacy_order"):
    dt = LarcDeviceType(name=name, category="larc",
                        default_flow=default_flow, is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_assignment_model_has_reason_columns(db):
    dt = _dt(db)
    a = LarcAssignment(chart_number="MRN1", patient_name="Doe, Jane",
                       device_type_id=dt.id, source_flow="pharmacy_order",
                       status="new", reason_for_request="Contraception",
                       reason_icd10="Z30.430")
    db.add(a); db.commit(); db.refresh(a)
    assert a.reason_for_request == "Contraception"
    assert a.reason_icd10 == "Z30.430"
