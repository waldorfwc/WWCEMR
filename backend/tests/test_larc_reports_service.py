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
