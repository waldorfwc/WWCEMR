"""GET /api/larc/to-bill — practice-owned, checked-out, not-yet-billed worklist."""
from datetime import datetime
from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType, LarcMilestone
from app.services.larc.workflow import spawn_milestones


def _dt(db, name="Mirena"):
    dt = LarcDeviceType(name=name, category="larc", default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    return dt


def _assignment(db, dt, ownership, our_id, *, checked_out=True, inserted=False,
                billed=False, co_at=None):
    dev = LarcDevice(our_id=our_id, device_type_id=dt.id, status="checked_out", ownership=ownership)
    db.add(dev); db.commit(); db.refresh(dev)
    a = LarcAssignment(chart_number=f"C{our_id}", patient_name=f"Pt {our_id}",
                       device_type_id=dt.id, device_id=dev.id, source_flow="in_stock",
                       status="inserted" if inserted else "in_progress")
    db.add(a); db.commit(); db.refresh(a)
    spawn_milestones(db, a); db.commit()
    by_kind = {m.kind: m for m in a.milestones}

    def mark(kind, when=None):
        m = by_kind.get(kind)
        if m:
            m.status = "done"
            m.completed_at = when or datetime(2026, 6, 1, 9, 0, 0)
    if checked_out:
        mark("device_checked_out", co_at)
    if inserted:
        mark("device_inserted")
    if billed:
        mark("billed")
    db.commit()
    return a


def test_to_bill_lists_practice_owned_checked_out_unbilled(client, db):
    dt = _dt(db)
    inserted = _assignment(db, dt, "wwc_owned", "WWC-1", inserted=True,
                           co_at=datetime(2026, 6, 2, 9, 0, 0))
    awaiting = _assignment(db, dt, "wwc_claimed", "WWC-2", inserted=False,
                           co_at=datetime(2026, 6, 1, 9, 0, 0))
    _assignment(db, dt, "patient_owned", "PT-1", inserted=True)
    _assignment(db, dt, "wwc_owned", "WWC-3", checked_out=False)
    _assignment(db, dt, "wwc_owned", "WWC-4", inserted=True, billed=True)

    r = client.get("/api/larc/to-bill")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [it["assignment_id"] for it in body["items"]]
    assert str(inserted.id) in ids and str(awaiting.id) in ids
    assert body["total"] == 2
    assert ids == [str(awaiting.id), str(inserted.id)]   # oldest checked-out first
    by_id = {it["assignment_id"]: it for it in body["items"]}
    assert by_id[str(inserted.id)]["inserted"] is True
    assert by_id[str(awaiting.id)]["inserted"] is False
    assert by_id[str(inserted.id)]["device_our_id"] == "WWC-1"
    assert by_id[str(awaiting.id)]["device_type_name"] == "Mirena"
