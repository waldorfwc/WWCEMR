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


def _inserted_in_range_q(db, date_from, date_to, location, device_type_id):
    return (_assignment_base(db, location, device_type_id)
            .filter(LarcAssignment.status.in_(("inserted", "billed")),
                    LarcAssignment.inserted_at.isnot(None),
                    LarcAssignment.inserted_at >= _dt_floor(date_from),
                    LarcAssignment.inserted_at < _dt_floor(date_to + timedelta(days=1))))


def insertions(db: Session, *, date_from: date, date_to: date,
               location: Optional[str] = None, device_type_id: Optional[str] = None) -> dict:
    rows = _inserted_in_range_q(db, date_from, date_to, location, device_type_id).all()
    cats = {t.id: t.category for t in db.query(LarcDeviceType).all()}
    by_category: dict = {}
    for a in rows:
        c = cats.get(a.device_type_id, "larc")
        by_category[c] = by_category.get(c, 0) + 1
    total = len(rows)
    length = (date_to - date_from).days + 1
    prior_to = date_from - timedelta(days=1)
    prior_from = prior_to - timedelta(days=length - 1)
    prior_total = _inserted_in_range_q(db, prior_from, prior_to, location, device_type_id).count()
    return {"total": total, "by_category": by_category, "prior_total": prior_total,
            "prior_from": prior_from, "prior_to": prior_to, "delta": total - prior_total}


def insertion_outcomes(db: Session, *, date_from: date, date_to: date,
                       location: Optional[str] = None,
                       device_type_id: Optional[str] = None) -> dict:
    """Period: insertion-visit outcomes from LarcCheckout (requested_at in range)."""
    from app.models.larc import LarcCheckout
    q = (db.query(LarcCheckout)
         .join(LarcAssignment, LarcCheckout.assignment_id == LarcAssignment.id)
         .filter(LarcAssignment.deleted_at.is_(None),
                 LarcCheckout.outcome.isnot(None),
                 LarcCheckout.requested_at >= _dt_floor(date_from),
                 LarcCheckout.requested_at < _dt_floor(date_to + timedelta(days=1)))
         )
    if device_type_id:
        q = q.filter(LarcAssignment.device_type_id == device_type_id)
    if location:
        q = (q.join(LarcDevice, LarcAssignment.device_id == LarcDevice.id)
               .filter(LarcDevice.location == location))
    rows = q.all()
    success = sum(1 for c in rows if c.outcome == "inserted")
    fu = sum(1 for c in rows if c.outcome == "failed_unused")
    fused = sum(1 for c in rows if c.outcome == "failed_used")
    attempts = success + fu + fused
    rate = round((fu + fused) / attempts, 2) if attempts else 0.0
    return {"success": success, "failed_unused": fu, "failed_used": fused,
            "total": attempts, "failure_rate": rate}
