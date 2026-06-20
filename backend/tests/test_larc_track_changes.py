from app.services.larc.workflow import (
    IN_STOCK_MILESTONES, PHARMACY_ORDER_MILESTONES, ALL_BUCKETS)


def test_no_appt_scheduled_milestone():
    kinds_in = [k for k, *_ in IN_STOCK_MILESTONES]
    kinds_ph = [k for k, *_ in PHARMACY_ORDER_MILESTONES]
    assert "appt_scheduled" not in kinds_in
    assert "appt_scheduled" not in kinds_ph
    assert "appt_scheduled" not in ALL_BUCKETS


from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.models.larc import LarcMilestone


def _ph_assignment_with_patient_device(db):
    dt = LarcDeviceType(name="Kyleena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    dev = LarcDevice(our_id="P-1", device_type_id=dt.id, status="assigned", ownership="patient_owned")
    db.add(dev); db.commit(); db.refresh(dev)
    a = LarcAssignment(chart_number="M9", patient_name="Roe, P", device_type_id=dt.id,
                       device_id=dev.id, source_flow="pharmacy_order", status="new")
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_patient_owned_billed_not_applicable(db):
    from app.services.larc.workflow import spawn_milestones
    a = _ph_assignment_with_patient_device(db)
    spawn_milestones(db, a); db.commit()
    billed = db.query(LarcMilestone).filter(
        LarcMilestone.assignment_id == a.id, LarcMilestone.kind == "billed").first()
    assert billed is None or billed.status == "not_applicable"


def test_practice_owned_billed_still_pending(db):
    from app.services.larc.workflow import spawn_milestones
    dt = LarcDeviceType(name="Liletta", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    dev = LarcDevice(our_id="W-1", device_type_id=dt.id, status="assigned", ownership="wwc_owned")
    db.add(dev); db.commit(); db.refresh(dev)
    a = LarcAssignment(chart_number="M10", patient_name="Roe, Q", device_type_id=dt.id,
                       device_id=dev.id, source_flow="in_stock", status="new")
    db.add(a); db.commit(); db.refresh(a)
    spawn_milestones(db, a); db.commit()
    billed = db.query(LarcMilestone).filter(
        LarcMilestone.assignment_id == a.id, LarcMilestone.kind == "billed").first()
    assert billed is not None and billed.status != "not_applicable"
