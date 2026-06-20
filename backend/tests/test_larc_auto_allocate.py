from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.services.larc.workflow import spawn_milestones
from app.services.larc.allocation import try_auto_allocate
from app.utils.dt import now_utc_naive
from datetime import date


def _setup(db, with_stock):
    dt = LarcDeviceType(name="Liletta", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    if with_stock:
        db.add(LarcDevice(our_id="S-1", device_type_id=dt.id, status="unassigned", ownership="wwc_owned"))
    a = LarcAssignment(chart_number="A1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="in_stock", status="in_progress",
                       benefits_verified_at=date.today(), patient_paid_at=now_utc_naive())
    db.add(a); db.commit(); db.refresh(a); spawn_milestones(db, a); db.commit()
    return a


def test_auto_allocate_binds_device(db):
    a = _setup(db, with_stock=True)
    res = try_auto_allocate(db, a); db.refresh(a)
    assert res["allocated"] is True
    assert a.device_id is not None
    assert a.needs_allocation_no_stock is False


def test_auto_allocate_no_stock_flags(db):
    a = _setup(db, with_stock=False)
    res = try_auto_allocate(db, a); db.refresh(a)
    assert res["allocated"] is False and res["reason"] == "no_stock"
    assert a.needs_allocation_no_stock is True


def test_auto_allocate_requires_gates(db):
    a = _setup(db, with_stock=True)
    a.patient_paid_at = None; db.commit()
    res = try_auto_allocate(db, a)
    assert res["allocated"] is False and res["reason"] == "gates_unmet"


def test_auto_allocate_skips_pharmacy_flow(db):
    a = _setup(db, with_stock=True)
    a.source_flow = "pharmacy_order"; db.commit()
    res = try_auto_allocate(db, a)
    assert res["allocated"] is False and res["reason"] == "not_applicable"
