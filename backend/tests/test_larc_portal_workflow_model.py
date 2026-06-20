from app.models.larc import LarcAssignment, LarcDeviceType


def _dt(db):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_assignment_has_portal_workflow_columns(db):
    dt = _dt(db)
    a = LarcAssignment(chart_number="M1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="in_stock", status="new",
                       sms_consent=True, sms_consented_by="patient:self",
                       portal_token_version=0, needs_allocation_no_stock=False)
    db.add(a); db.commit(); db.refresh(a)
    assert a.sms_consent is True
    assert a.portal_token_version == 0
    assert a.needs_allocation_no_stock is False
    assert a.sms_consented_at is None
