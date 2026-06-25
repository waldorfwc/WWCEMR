"""Consolidated return-to-stock outcomes on /larc/assignments/{id}/outcome.

LARC returns (appointment_canceled / returned_mistake) free the device to the
general pool but KEEP the assignment with the patient (re-assignable). Office
returns (returned_defective) send the device to the manufacturer-return queue
and close the assignment.
"""
from app.models.larc import LarcDevice, LarcDeviceType, LarcAssignment


def _setup(db, category="larc", device_status="checked_out", *, with_device=True):
    dt = LarcDeviceType(name=f"Type-{category}", category=category,
                        default_flow="in_stock", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    d = None
    if with_device:
        d = LarcDevice(our_id="D-RT1", device_type_id=dt.id,
                       status=device_status, location="white_plains")
        db.add(d); db.commit(); db.refresh(d)
    a = LarcAssignment(chart_number="RT1", patient_name="Doe, J",
                       device_type_id=dt.id, device_id=(d.id if d else None),
                       source_flow="in_stock", status="in_progress", is_active=True)
    db.add(a); db.commit(); db.refresh(a)
    return a, d, dt


def test_appointment_canceled_returns_device_keeps_assignment(client, db):
    a, d, _ = _setup(db, "larc")
    r = client.post(f"/api/larc/assignments/{a.id}/outcome",
                    json={"outcome": "appointment_canceled"})
    assert r.status_code == 200, r.text
    db.refresh(a); db.refresh(d)
    assert d.status == "unassigned"     # back to general stock
    assert a.device_id is None          # device freed from this allocation
    assert a.is_active is True          # assignment stays with the patient
    assert a.status == "in_progress"    # re-assignable
    assert a.failure_reason is None     # the assignment continues — not a failure


def test_returned_mistake_returns_device_keeps_assignment(client, db):
    a, d, _ = _setup(db, "larc")
    r = client.post(f"/api/larc/assignments/{a.id}/outcome",
                    json={"outcome": "returned_mistake"})
    assert r.status_code == 200, r.text
    db.refresh(a); db.refresh(d)
    assert d.status == "unassigned"
    assert a.device_id is None and a.is_active is True


def test_returned_defective_office_queues_manufacturer_and_closes(client, db):
    a, d, _ = _setup(db, "office_procedure")
    r = client.post(f"/api/larc/assignments/{a.id}/outcome",
                    json={"outcome": "returned_defective"})
    assert r.status_code == 200, r.text
    db.refresh(a); db.refresh(d)
    assert d.status == "defective"      # manufacturer-return queue
    assert a.is_active is False         # assignment closed


def test_larc_reason_rejected_for_office_device(client, db):
    a, _, _ = _setup(db, "office_procedure")
    r = client.post(f"/api/larc/assignments/{a.id}/outcome",
                    json={"outcome": "appointment_canceled"})
    assert r.status_code == 422


def test_office_reason_rejected_for_larc_device(client, db):
    a, _, _ = _setup(db, "larc")
    r = client.post(f"/api/larc/assignments/{a.id}/outcome",
                    json={"outcome": "returned_defective"})
    assert r.status_code == 422


def test_return_reason_requires_an_allocated_device(client, db):
    a, _, _ = _setup(db, "larc", with_device=False)
    r = client.post(f"/api/larc/assignments/{a.id}/outcome",
                    json={"outcome": "appointment_canceled"})
    assert r.status_code == 422
