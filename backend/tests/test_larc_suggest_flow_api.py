"""Authenticated tests for POST /api/larc/assignments/suggest-flow."""
from app.models.larc import LarcDeviceType, LarcDevice


def _dt(db, name, default_flow):
    dt = LarcDeviceType(name=name, category=("office_procedure"
                        if default_flow == "office_procedure" else "larc"),
                        default_flow=default_flow, is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_suggest_flow_in_stock(client, db):
    dt = _dt(db, "Mirena", "pharmacy_order")
    db.add(LarcDevice(our_id="WWC-10", device_type_id=dt.id, status="unassigned"))
    db.commit()
    r = client.post("/api/larc/assignments/suggest-flow",
                    json={"device_type_id": str(dt.id)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggested_flow"] == "in_stock"
    assert body["in_stock_count"] == 1
    assert set(body["allowed_flows"]) == {"in_stock", "pharmacy_order"}


def test_suggest_flow_pharmacy_when_empty(client, db):
    dt = _dt(db, "Kyleena", "pharmacy_order")
    r = client.post("/api/larc/assignments/suggest-flow",
                    json={"device_type_id": str(dt.id)})
    assert r.status_code == 200, r.text
    assert r.json()["suggested_flow"] == "pharmacy_order"
    assert r.json()["allowed_flows"] == ["pharmacy_order"]


def test_suggest_flow_unknown_device_type_404(client, db):
    r = client.post("/api/larc/assignments/suggest-flow",
                    json={"device_type_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 404
