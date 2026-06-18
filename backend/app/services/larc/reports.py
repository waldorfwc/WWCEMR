"""LARC (Device Tracking) Reports aggregations. Each tile is a pure function
over the assignment/device data, parameterized by an optional location +
device-type filter and (for period tiles) a date range. No persistence."""
from __future__ import annotations

import csv
import io
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


def billing_backlog(db: Session, *, location: Optional[str] = None,
                    device_type_id: Optional[str] = None) -> dict:
    """Snapshot: inserted assignments not yet billed."""
    n = (_assignment_base(db, location, device_type_id)
         .filter(LarcAssignment.status == "inserted",
                 LarcAssignment.billed_at.is_(None)).count())
    return {"count": n}


def owed_patients(db: Session, *, location: Optional[str] = None,
                  device_type_id: Optional[str] = None) -> dict:
    """Snapshot: open owed-patient rows + failed-used assignments awaiting a
    replacement (the 'failed_replacement_unrequested' state)."""
    from app.models.larc import LarcOwedPatient
    oq = db.query(LarcOwedPatient).filter(LarcOwedPatient.resolved_at.is_(None))
    if device_type_id:
        oq = oq.filter(LarcOwedPatient.original_device_type_id == device_type_id)
    owed_count = oq.count()
    awaiting = (_assignment_base(db, location, device_type_id)
                .filter(LarcAssignment.status == "failed_used",
                        LarcAssignment.replacement_assignment_id.is_(None)).count())
    return {"owed_count": owed_count, "awaiting_replacement": awaiting,
            "total": owed_count + awaiting}


def _instock_devices_q(db, location, device_type_id):
    q = db.query(LarcDevice).filter(LarcDevice.status.in_(("unassigned", "received")))
    if location:
        q = q.filter(LarcDevice.location == location)
    if device_type_id:
        q = q.filter(LarcDevice.device_type_id == device_type_id)
    return q


def inventory_health(db: Session, *, location: Optional[str] = None,
                     device_type_id: Optional[str] = None,
                     today: Optional[date] = None) -> dict:
    """Snapshot: in-stock devices by type, expiring <=90 days, and device types
    below their reorder threshold."""
    today = today or now_utc_naive().date()
    horizon = today + timedelta(days=90)
    devices = _instock_devices_q(db, location, device_type_id).all()
    types = {t.id: t for t in db.query(LarcDeviceType).all()}
    by_type: dict = {}
    expiring = 0
    onhand_by_type: dict = {}
    for d in devices:
        name = types[d.device_type_id].name if d.device_type_id in types else "Unknown"
        by_type[name] = by_type.get(name, 0) + 1
        onhand_by_type[d.device_type_id] = onhand_by_type.get(d.device_type_id, 0) + 1
        if d.expiration_date and d.expiration_date <= horizon:
            expiring += 1
    below = 0
    for tid, t in types.items():
        if t.reorder_threshold is None:
            continue
        if device_type_id and tid != device_type_id:
            continue
        if onhand_by_type.get(tid, 0) < int(t.reorder_threshold):
            below += 1
    return {"total_on_hand": len(devices), "by_type": by_type,
            "expiring": expiring, "below_reorder": below}


def _assignment_row(a, type_names: dict) -> dict:
    dev = a.device
    return {
        "assignment_id": str(a.id),
        "chart_number": a.chart_number,
        "patient_name": a.patient_name,
        "device_type": type_names.get(a.device_type_id),
        "ownership": (dev.ownership if dev else None),
        "location": (dev.location if dev else None),
        "status": a.status,
        "source_flow": a.source_flow,
        "inserted_at": a.inserted_at.strftime("%m/%d/%Y") if a.inserted_at else None,
        "billed_at": a.billed_at.strftime("%m/%d/%Y") if a.billed_at else None,
    }


VALID_TILES = ("workflow_funnel", "outstanding_enrollment", "insertions",
               "billing_backlog", "owed_patients", "inventory_health",
               "insertion_outcomes")


def rows_for(db: Session, tile: str, *, date_from: date, date_to: date,
             location: Optional[str] = None, device_type_id: Optional[str] = None,
             bucket: Optional[str] = None, today: Optional[date] = None) -> list[dict]:
    today = today or now_utc_naive().date()
    type_names = {t.id: t.name for t in db.query(LarcDeviceType).all()}

    if tile in ("workflow_funnel", "outstanding_enrollment"):
        q = (_assignment_base(db, location, device_type_id)
             .options(joinedload(LarcAssignment.milestones),
                      joinedload(LarcAssignment.device)))
        out = []
        for a in q.all():
            buckets = assignment_buckets(a, today)
            if tile == "outstanding_enrollment":
                buckets = {b for b in buckets if b in _ENROLLMENT_STAGES}
            if not buckets:
                continue
            if bucket and bucket not in buckets:
                continue
            r = _assignment_row(a, type_names)
            r["bucket"] = "; ".join(sorted(buckets))
            out.append(r)
        return out

    if tile == "insertions":
        rows = _inserted_in_range_q(db, date_from, date_to, location, device_type_id).all()
        cats = {t.id: t.category for t in db.query(LarcDeviceType).all()}
        out = []
        for a in rows:
            cat = cats.get(a.device_type_id, "larc")
            if bucket and cat != bucket:
                continue
            r = _assignment_row(a, type_names)
            r["category"] = cat
            out.append(r)
        return out

    if tile == "billing_backlog":
        q = (_assignment_base(db, location, device_type_id)
             .filter(LarcAssignment.status == "inserted",
                     LarcAssignment.billed_at.is_(None))
             .options(joinedload(LarcAssignment.device)))
        return [_assignment_row(a, type_names) for a in q.all()]

    if tile == "owed_patients":
        from app.models.larc import LarcOwedPatient
        if bucket == "awaiting_replacement":
            q = (_assignment_base(db, location, device_type_id)
                 .filter(LarcAssignment.status == "failed_used",
                         LarcAssignment.replacement_assignment_id.is_(None))
                 .options(joinedload(LarcAssignment.device)))
            return [_assignment_row(a, type_names) for a in q.all()]
        oq = db.query(LarcOwedPatient).filter(LarcOwedPatient.resolved_at.is_(None))
        if device_type_id:
            oq = oq.filter(LarcOwedPatient.original_device_type_id == device_type_id)
        return [{"chart_number": o.chart_number, "patient_name": o.patient_name,
                 "device_type": type_names.get(o.original_device_type_id),
                 "owed_since": o.owed_since.strftime("%m/%d/%Y") if o.owed_since else None}
                for o in oq.all()]

    if tile == "inventory_health":
        horizon = today + timedelta(days=90)
        devices = _instock_devices_q(db, location, device_type_id).all()
        out = []
        for d in devices:
            if bucket == "expiring":
                if not (d.expiration_date and d.expiration_date <= horizon):
                    continue
            elif bucket and bucket not in ("expiring", "below_reorder"):
                if str(d.device_type_id) != bucket:
                    continue
            out.append({"our_id": d.our_id, "device_type": type_names.get(d.device_type_id),
                        "location": d.location, "ownership": d.ownership,
                        "expiration_date": d.expiration_date.strftime("%m/%d/%Y") if d.expiration_date else None})
        return out

    if tile == "insertion_outcomes":
        from app.models.larc import LarcCheckout
        q = (db.query(LarcCheckout, LarcAssignment)
             .join(LarcAssignment, LarcCheckout.assignment_id == LarcAssignment.id)
             .filter(LarcAssignment.deleted_at.is_(None),
                     LarcCheckout.outcome.isnot(None),
                     LarcCheckout.requested_at >= _dt_floor(date_from),
                     LarcCheckout.requested_at < _dt_floor(date_to + timedelta(days=1))))
        if device_type_id:
            q = q.filter(LarcAssignment.device_type_id == device_type_id)
        if location:
            q = q.join(LarcDevice, LarcAssignment.device_id == LarcDevice.id).filter(LarcDevice.location == location)
        _label = {"inserted": "success"}
        out = []
        for c, a in q.all():
            key = _label.get(c.outcome, c.outcome)
            if bucket and key != bucket:
                continue
            out.append({"checkout_id": str(c.id), "chart_number": a.chart_number,
                        "patient_name": a.patient_name, "device_type": type_names.get(a.device_type_id),
                        "outcome": c.outcome,
                        "requested_at": c.requested_at.strftime("%m/%d/%Y") if c.requested_at else None})
        return out

    raise ValueError(f"unknown tile: {tile}")


def rows_to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    fields = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("; ".join(map(str, v)) if isinstance(v, list) else v)
                    for k, v in r.items()})
    return buf.getvalue()
