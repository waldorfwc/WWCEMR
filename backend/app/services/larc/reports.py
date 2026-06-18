"""LARC (Device Tracking) Reports aggregations. Each tile is a pure function
over the assignment/device data, parameterized by an optional location +
device-type filter and (for period tiles) a date range. No persistence."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models.larc import (LarcAssignment, LarcDevice, LarcDeviceType)
from app.services.larc.workflow import assignment_buckets
from app.utils.dt import now_utc_naive


def _dt_floor(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _assignment_base(db: Session, location: Optional[str], device_type_id: Optional[str]):
    """LarcAssignment query: not soft-deleted; device-type on the assignment,
    location via the assigned device (assignments with no device yet don't match
    a specific location filter)."""
    q = db.query(LarcAssignment).filter(LarcAssignment.deleted_at.is_(None))
    if device_type_id:
        q = q.filter(LarcAssignment.device_type_id == device_type_id)
    if location:
        q = (q.join(LarcDevice, LarcAssignment.device_id == LarcDevice.id)
               .filter(LarcDevice.location == location))
    return q


def device_types(db: Session) -> list[dict]:
    rows = db.query(LarcDeviceType).order_by(LarcDeviceType.name).all()
    return [{"id": str(t.id), "name": t.name, "category": t.category} for t in rows]


def workflow_funnel(db: Session, *, location: Optional[str] = None,
                    device_type_id: Optional[str] = None,
                    today: Optional[date] = None) -> dict:
    """Snapshot: active assignments tallied by workload bucket (assignment_buckets)."""
    today = today or now_utc_naive().date()
    q = (_assignment_base(db, location, device_type_id)
         .options(joinedload(LarcAssignment.milestones),
                  joinedload(LarcAssignment.device)))
    by_bucket: dict = {}
    for a in q.all():
        for b in assignment_buckets(a, today):
            by_bucket[b] = by_bucket.get(b, 0) + 1
    return {"by_bucket": by_bucket}


_ENROLLMENT_STAGES = ("needs_enrollment", "needs_fax",
                      "awaiting_receipt", "received_not_notified")


def outstanding_enrollment(db: Session, *, location: Optional[str] = None,
                           device_type_id: Optional[str] = None,
                           today: Optional[date] = None) -> dict:
    """Snapshot: the pharmacy-order enrollment pipeline — assignments at each
    enrollment stage (a focused subset of the funnel buckets)."""
    today = today or now_utc_naive().date()
    q = (_assignment_base(db, location, device_type_id)
         .options(joinedload(LarcAssignment.milestones),
                  joinedload(LarcAssignment.device)))
    by_stage = {s: 0 for s in _ENROLLMENT_STAGES}
    total = 0
    for a in q.all():
        buckets = assignment_buckets(a, today)
        stages = [s for s in _ENROLLMENT_STAGES if s in buckets]
        if stages:
            total += 1
            for s in stages:
                by_stage[s] += 1
    return {"by_stage": by_stage, "total": total}
