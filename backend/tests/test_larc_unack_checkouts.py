"""Regression: unacknowledged-checkouts dashboard alert + acknowledge flow."""
from datetime import timedelta
from app.utils.dt import now_utc_naive
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType, LarcCheckout


def _setup(db, *, requested_hours_ago):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    dev = LarcDevice(our_id="W1", device_type_id=dt.id, status="checked_out", ownership="wwc_owned")
    db.add(dev); db.commit(); db.refresh(dev)
    a = LarcAssignment(chart_number="MRN1", patient_name="Doe, J", device_type_id=dt.id,
                       device_id=dev.id, source_flow="in_stock", status="checked_out")
    db.add(a); db.commit(); db.refresh(a)
    c = LarcCheckout(assignment_id=a.id, device_id=dev.id, requested_by="ma@wwc.com",
                     approval_status="approved",
                     requested_at=now_utc_naive() - timedelta(hours=requested_hours_ago))
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_old_unacked_checkout_listed_then_cleared_by_ack(client, db):
    c = _setup(db, requested_hours_ago=48)
    body = client.get("/api/larc/dashboard").json()
    ids = [u["checkout_id"] for u in body["unacknowledged_checkouts"]]
    assert str(c.id) in ids
    r = client.post(f"/api/larc/checkouts/{c.id}/acknowledge")
    assert r.status_code == 200, r.text
    assert r.json()["acknowledged_at"]
    body2 = client.get("/api/larc/dashboard").json()
    ids2 = [u["checkout_id"] for u in body2["unacknowledged_checkouts"]]
    assert str(c.id) not in ids2


def test_recent_checkout_not_listed(client, db):
    c = _setup(db, requested_hours_ago=1)
    body = client.get("/api/larc/dashboard").json()
    ids = [u["checkout_id"] for u in body["unacknowledged_checkouts"]]
    assert str(c.id) not in ids
