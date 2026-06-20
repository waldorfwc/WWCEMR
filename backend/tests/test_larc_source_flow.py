"""Unit tests for the shared LARC source-flow decision + suggestion."""
from app.models.larc import LarcDeviceType, LarcDevice
from app.services.larc.source_flow import pick_source_flow, suggest_flow


def _dt(db, name, default_flow):
    dt = LarcDeviceType(name=name, category=("office_procedure"
                        if default_flow == "office_procedure" else "larc"),
                        default_flow=default_flow, is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _stock(db, dt, our_id):
    d = LarcDevice(our_id=our_id, device_type_id=dt.id, status="unassigned")
    db.add(d); db.commit()
    return d


def test_pick_in_stock_when_unassigned_device_exists(db):
    dt = _dt(db, "Mirena", "pharmacy_order")
    _stock(db, dt, "WWC-1")
    assert pick_source_flow(db, dt) == "in_stock"


def test_pick_pharmacy_when_no_stock_and_default_pharmacy(db):
    dt = _dt(db, "Kyleena", "pharmacy_order")
    assert pick_source_flow(db, dt) == "pharmacy_order"


def test_pick_office_when_default_office_and_no_stock(db):
    dt = _dt(db, "NovaSure", "office_procedure")
    assert pick_source_flow(db, dt) == "office_procedure"


def test_suggest_normal_device_offers_stock_and_pharmacy(db):
    dt = _dt(db, "Skyla", "pharmacy_order")
    _stock(db, dt, "WWC-2")
    s = suggest_flow(db, dt)
    assert s["suggested_flow"] == "in_stock"
    assert s["in_stock_count"] == 1
    assert set(s["allowed_flows"]) == {"in_stock", "pharmacy_order"}


def test_suggest_pharmacy_when_no_stock(db):
    dt = _dt(db, "Paragard", "pharmacy_order")
    s = suggest_flow(db, dt)
    assert s["suggested_flow"] == "pharmacy_order"
    assert s["in_stock_count"] == 0
    assert s["allowed_flows"] == ["pharmacy_order"]


def test_suggest_consumable_never_offers_pharmacy(db):
    dt = _dt(db, "Bensta", "office_procedure")
    s = suggest_flow(db, dt)
    assert s["suggested_flow"] == "office_procedure"
    assert "pharmacy_order" not in s["allowed_flows"]
    assert s["allowed_flows"] == ["office_procedure"]
