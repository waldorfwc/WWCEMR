from datetime import date, timedelta
from app.utils.dt import now_utc_naive
from app.models.larc import (
    LarcAssignment, LarcAuditEvent, LarcDevice, LarcDeviceType, LarcOwedPatient,
)
from app.services.larc.sweeps import (
    sweep_stale_assignments, sweep_expiry_hold,
)


def _dt(db):
    dt = LarcDeviceType(name="Mirena", category="larc",
                        default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _device(db, dt, *, ownership, our_id, expiration_date=None):
    d = LarcDevice(our_id=our_id, device_type_id=dt.id, status="assigned",
                   ownership=ownership, expiration_date=expiration_date)
    db.add(d); db.commit(); db.refresh(d)
    return d


def _assignment(db, dt, d, *, created_days_ago=400, received_days_ago=200):
    a = LarcAssignment(
        chart_number="12345", patient_name="Doe, Jane",
        device_id=d.id, device_type_id=dt.id,
        status="new", is_active=True,
        source_flow="pharmacy_order",
    )
    db.add(a); db.commit(); db.refresh(a)
    # Force the timestamps after insert (created_at has a server default).
    a.created_at = now_utc_naive() - timedelta(days=created_days_ago)
    a.device_received_at = now_utc_naive() - timedelta(days=received_days_ago)
    db.commit(); db.refresh(a)
    return a


def _ownership_events(db, device_id):
    return (db.query(LarcAuditEvent)
              .filter(LarcAuditEvent.device_id == device_id,
                      LarcAuditEvent.action == "ownership_changed")
              .all())


def test_stale_sweep_claims_patient_owned_device(db):
    dt = _dt(db)
    d = _device(db, dt, ownership="patient_owned", our_id="P1")
    _assignment(db, dt, d, created_days_ago=400, received_days_ago=200)

    sweep_stale_assignments(db)

    db.refresh(d)
    assert d.ownership == "wwc_claimed"
    assert d.status == "unassigned"
    owed = db.query(LarcOwedPatient).filter(
        LarcOwedPatient.chart_number == "12345",
        LarcOwedPatient.resolved_at.is_(None)).all()
    assert len(owed) == 1
    assert len(_ownership_events(db, d.id)) == 1


def test_stale_sweep_leaves_wwc_owned_ownership_alone(db):
    dt = _dt(db)
    d = _device(db, dt, ownership="wwc_owned", our_id="W1")
    _assignment(db, dt, d, created_days_ago=400, received_days_ago=200)

    sweep_stale_assignments(db)

    db.refresh(d)
    assert d.ownership == "wwc_owned"          # untouched
    assert d.status == "unassigned"            # still reallocated
    assert _ownership_events(db, d.id) == []   # no ownership_changed event


def test_expiry_sweep_claims_patient_owned_device(db):
    dt = _dt(db)
    # Expires within the 365-day hold window -> expiry sweep catches it.
    d = _device(db, dt, ownership="patient_owned", our_id="P2",
                expiration_date=date.today() + timedelta(days=30))
    _assignment(db, dt, d, created_days_ago=10, received_days_ago=5)

    sweep_expiry_hold(db)

    db.refresh(d)
    assert d.ownership == "wwc_claimed"
    assert d.status == "unassigned"
    assert len(_ownership_events(db, d.id)) == 1
