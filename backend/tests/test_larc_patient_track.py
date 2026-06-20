from app.models.larc import LarcAssignment, LarcDeviceType
from app.services.larc.workflow import spawn_milestones
from app.services.larc.patient_track import patient_track


def _mk(db, flow):
    dt = LarcDeviceType(name="Mirena", category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="T1", patient_name="Doe, J", device_type_id=dt.id,
                       source_flow=flow, status="new")
    db.add(a); db.commit(); db.refresh(a)
    spawn_milestones(db, a); db.commit()
    return a


def test_pharmacy_track_shape(db):
    a = _mk(db, "pharmacy_order")
    t = patient_track(a)
    assert t["track"] == "pharmacy"
    assert [s["key"] for s in t["steps"]] == [
        "request_received", "enrollment_completed", "enrollment_faxed",
        "device_received", "patient_notified"]
    assert t["steps"][0]["status"] == "done"
    assert t["steps"][1]["status"] == "current"


def test_practice_track_shape(db):
    a = _mk(db, "in_stock")
    t = patient_track(a)
    assert t["track"] == "practice_owned"
    assert [s["key"] for s in t["steps"]] == [
        "request_received", "responsibility_determined", "responsibility_satisfied",
        "device_allocated", "patient_notified"]
