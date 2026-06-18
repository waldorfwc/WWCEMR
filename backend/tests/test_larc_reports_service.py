"""LARC Reports aggregation service."""
from datetime import date, datetime, timedelta

from app.models.larc import (LarcAssignment, LarcDevice, LarcDeviceType)
from app.services.larc import reports as rpt


def _dtype(db, name="Liletta", category="larc", reorder=None):
    t = LarcDeviceType(name=name, category=category, reorder_threshold=reorder)
    db.add(t); db.commit(); db.refresh(t)
    return t


def _device(db, dtype, *, status="unassigned", ownership="wwc_owned",
            location="white_plains", our_id="LAR-1", expires=None):
    d = LarcDevice(our_id=our_id, device_type_id=dtype.id, status=status,
                   ownership=ownership, location=location, expiration_date=expires)
    db.add(d); db.commit(); db.refresh(d)
    return d


def _assignment(db, dtype, *, status="new", source_flow="in_stock",
                device=None, chart="M1", **kw):
    a = LarcAssignment(chart_number=chart, patient_name=f"Pt {chart}",
                       status=status, source_flow=source_flow,
                       device_type_id=dtype.id, device_id=(device.id if device else None),
                       **kw)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_device_types_list(db):
    _dtype(db, "Liletta", "larc")
    _dtype(db, "NovaSure", "office_procedure")
    out = rpt.device_types(db)
    assert {t["name"] for t in out} == {"Liletta", "NovaSure"}
    assert all({"id", "name", "category"} <= set(t) for t in out)


def test_workflow_funnel_buckets(db):
    t = _dtype(db)
    d = _device(db, t, status="checked_out")
    _assignment(db, t, status="checked_out", device=d, chart="F1")
    out = rpt.workflow_funnel(db, location=None, device_type_id=None)
    assert isinstance(out["by_bucket"], dict)
    _assignment(db, t, status="billed", device=_device(db, t, our_id="LAR-2"), chart="F2")
    out2 = rpt.workflow_funnel(db, location=None, device_type_id=None)
    assert "billed" not in out2["by_bucket"]


def test_outstanding_enrollment(db):
    t = _dtype(db)
    _assignment(db, t, status="in_progress", source_flow="pharmacy_order", chart="E1")
    out = rpt.outstanding_enrollment(db, location=None, device_type_id=None)
    assert out["total"] >= 0
    assert set(out["by_stage"]) == {"needs_enrollment", "needs_fax",
                                    "awaiting_receipt", "received_not_notified"}


def test_insertions_in_range_with_prior(db):
    t1 = _dtype(db, "Liletta", "larc")
    t2 = _dtype(db, "NovaSure", "office_procedure")
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    _assignment(db, t1, status="inserted", chart="I1",
                inserted_at=datetime(2026, 6, 10))
    _assignment(db, t2, status="billed", chart="I2",
                inserted_at=datetime(2026, 6, 20))
    _assignment(db, t1, status="inserted", chart="I3",
                inserted_at=datetime(2026, 5, 15))   # prior period
    out = rpt.insertions(db, date_from=df, date_to=dt, location=None, device_type_id=None)
    assert out["total"] == 2
    assert out["by_category"] == {"larc": 1, "office_procedure": 1}
    assert out["prior_total"] == 1
    assert out["delta"] == 1
    assert out["prior_from"] == date(2026, 5, 2) and out["prior_to"] == date(2026, 5, 31)


def test_insertion_outcomes(db):
    from app.models.larc import LarcCheckout
    t = _dtype(db)
    d = _device(db, t)
    a = _assignment(db, t, device=d, chart="O1")
    df, dt = date(2026, 6, 1), date(2026, 6, 30)
    for oc in ("inserted", "failed_unused", "failed_used", "patient_no_show"):
        db.add(LarcCheckout(assignment_id=a.id, device_id=d.id, requested_by="ma@x.com",
                            outcome=oc, requested_at=datetime(2026, 6, 15)))
    db.commit()
    out = rpt.insertion_outcomes(db, date_from=df, date_to=dt, location=None, device_type_id=None)
    assert out["success"] == 1 and out["failed_unused"] == 1 and out["failed_used"] == 1
    assert out["total"] == 3
    assert out["failure_rate"] == round(2 / 3, 2)


def test_billing_backlog(db):
    t = _dtype(db)
    _assignment(db, t, status="inserted", chart="B1", billed_at=None)
    _assignment(db, t, status="inserted", chart="B2", billed_at=datetime(2026, 6, 3))
    _assignment(db, t, status="new", chart="B3")
    out = rpt.billing_backlog(db, location=None, device_type_id=None)
    assert out["count"] == 1


def test_owed_patients(db):
    from app.models.larc import LarcOwedPatient
    t = _dtype(db)
    a = _assignment(db, t, chart="OW1")
    db.add(LarcOwedPatient(chart_number="OW1", patient_name="Pt OW1",
                           original_assignment_id=a.id, original_device_type_id=t.id))
    db.add(LarcOwedPatient(chart_number="OW2", patient_name="Pt OW2",
                           original_assignment_id=a.id, original_device_type_id=t.id,
                           resolved_at=datetime(2026, 6, 1)))
    db.commit()
    out = rpt.owed_patients(db, location=None, device_type_id=None)
    assert out["owed_count"] == 1


def test_inventory_health(db):
    from datetime import date as _d
    t = _dtype(db, "Liletta", "larc", reorder=5)
    _device(db, t, status="unassigned", our_id="D1", location="white_plains",
            expires=_d(2026, 7, 1))
    _device(db, t, status="inserted", our_id="D2")
    out = rpt.inventory_health(db, location=None, device_type_id=None, today=_d(2026, 6, 15))
    assert out["total_on_hand"] == 1
    assert out["expiring"] == 1
    assert out["below_reorder"] == 1
