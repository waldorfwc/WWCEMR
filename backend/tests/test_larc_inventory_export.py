from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType


def _dt(db):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def test_export_csv_on_hand_with_assignee(client, db):
    dt = _dt(db)
    assigned = LarcDevice(our_id="W-AS", device_type_id=dt.id, status="assigned",
                          ownership="wwc_owned", manufacturer_lot="LOT9", location="white_plains")
    db.add(assigned); db.commit(); db.refresh(assigned)
    db.add(LarcAssignment(chart_number="MRN5", patient_name="Doe, Jane", device_type_id=dt.id,
                          device_id=assigned.id, source_flow="in_stock", status="in_progress",
                          is_active=True))
    db.add(LarcDevice(our_id="W-UN", device_type_id=dt.id, status="unassigned",
                      ownership="wwc_owned", manufacturer_lot="LOT1", location="white_plains"))
    db.add(LarcDevice(our_id="W-BILL", device_type_id=dt.id, status="billed", ownership="wwc_owned"))
    db.commit()
    r = client.get("/api/larc/devices/export.csv")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    text = r.text
    assert "W-AS" in text and "W-UN" in text
    assert "W-BILL" not in text
    assert "Doe, Jane" in text
    assert "LOT9" in text


def test_export_pdf_returns_pdf(client, db):
    dt = _dt(db)
    db.add(LarcDevice(our_id="W-UN", device_type_id=dt.id, status="unassigned",
                      ownership="wwc_owned", manufacturer_lot="LOT1", location="white_plains"))
    db.commit()
    r = client.get("/api/larc/devices/export.pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
