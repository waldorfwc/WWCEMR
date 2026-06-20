from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from datetime import date


def _ready_in_stock(db, responsibility=None):
    dt = LarcDeviceType(name="Liletta", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    db.add(LarcDevice(our_id="S-9", device_type_id=dt.id, status="unassigned", ownership="wwc_owned"))
    a = LarcAssignment(chart_number="B1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow="in_stock", status="in_progress",
                       benefits_verified_at=date.today(), patient_responsibility=responsibility)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_record_payment_triggers_auto_allocate(client, db):
    a = _ready_in_stock(db, responsibility=100)
    r = client.post(f"/api/larc/assignments/{a.id}/payment-received", json={"amount": 100})
    assert r.status_code == 200, r.text
    db.refresh(a)
    assert a.patient_paid_at is not None
    assert a.device_id is not None      # auto-allocated


def test_zero_responsibility_auto_satisfies_on_benefits(client, db):
    a = _ready_in_stock(db, responsibility=0)
    a.benefits_verified_at = None; db.commit()    # let the endpoint set it
    r = client.post(f"/api/larc/assignments/{a.id}/benefits", json={
        "allowed_amount": 0, "deductible": 0, "deductible_met": 0, "copay": 0,
        "coinsurance_pct": 0, "oop_max": 0, "oop_met": 0})
    assert r.status_code == 200, r.text
    db.refresh(a)
    assert a.patient_paid_at is not None     # auto-satisfied (no balance)
    assert a.device_id is not None           # and auto-allocated
