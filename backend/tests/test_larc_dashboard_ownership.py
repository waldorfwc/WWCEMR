from app.models.larc import LarcDevice, LarcDeviceType


def _dt(db):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_dashboard_on_hand_by_ownership(db, client):
    dt = _dt(db)
    db.add(LarcDevice(our_id="W1", device_type_id=dt.id, status="unassigned", ownership="wwc_owned"))
    db.add(LarcDevice(our_id="W2", device_type_id=dt.id, status="assigned",   ownership="wwc_owned"))
    db.add(LarcDevice(our_id="P1", device_type_id=dt.id, status="received",   ownership="patient_owned"))
    db.add(LarcDevice(our_id="C1", device_type_id=dt.id, status="unassigned", ownership="wwc_claimed"))
    db.add(LarcDevice(our_id="B1", device_type_id=dt.id, status="billed",      ownership="wwc_owned"))
    db.add(LarcDevice(our_id="X1", device_type_id=dt.id, status="checked_out", ownership="wwc_owned"))
    db.commit()
    body = client.get("/api/larc/dashboard").json()
    own = body["on_hand_by_ownership"]
    assert own["wwc_owned"] == 2
    assert own["patient_owned"] == 1
    assert own["wwc_claimed"] == 1
