"""Surgery Reports aggregations. Each tile is a pure function over the Surgery
data, parameterized by an optional facility + surgeon filter and (for period
tiles) a date range. No persistence; computed on request."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.surgery import Surgery
from app.services.surgery.step_engine import _state


def _base_query(db: Session, facility: Optional[str], surgeon: Optional[str]):
    q = db.query(Surgery).filter(Surgery.deleted_at.is_(None))  # exclude soft-deleted
    if facility:
        q = q.filter(Surgery.selected_facility == facility)
    if surgeon:
        q = q.filter(Surgery.surgeon_primary == surgeon)
    return q


def _dt_floor(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _completed_in_range_q(db, date_from, date_to, facility, surgeon):
    """Surgeries with completed_at within [date_from, date_to] (inclusive)."""
    return (_base_query(db, facility, surgeon)
            .filter(Surgery.completed_at.isnot(None),
                    Surgery.completed_at >= _dt_floor(date_from),
                    Surgery.completed_at < _dt_floor(date_to + timedelta(days=1))))


def status_funnel(db: Session, *, facility: Optional[str] = None,
                  surgeon: Optional[str] = None) -> dict:
    """Snapshot: count of surgeries by internal status (frontend maps labels)."""
    rows = (_base_query(db, facility, surgeon)
            .with_entities(Surgery.status, func.count(Surgery.id))
            .group_by(Surgery.status).all())
    return {"by_status": {status: int(n) for status, n in rows}}


def completed(db: Session, *, date_from: date, date_to: date,
              facility: Optional[str] = None, surgeon: Optional[str] = None) -> dict:
    """Period: surgeries completed in range, split by classification, vs the
    immediately-preceding equal-length period."""
    rows = (_completed_in_range_q(db, date_from, date_to, facility, surgeon)
            .with_entities(Surgery.procedure_classification, func.count(Surgery.id))
            .group_by(Surgery.procedure_classification).all())
    by_cls = {(cls or "unspecified"): int(n) for cls, n in rows}
    total = sum(by_cls.values())
    length = (date_to - date_from).days + 1
    prior_to = date_from - timedelta(days=1)
    prior_from = prior_to - timedelta(days=length - 1)
    prior_total = _completed_in_range_q(db, prior_from, prior_to, facility, surgeon).count()
    return {"total": total, "by_classification": by_cls,
            "prior_total": prior_total, "prior_from": prior_from,
            "prior_to": prior_to, "delta": total - prior_total}


def cycle_time(db: Session, *, date_from: date, date_to: date,
               facility: Optional[str] = None, surgeon: Optional[str] = None) -> dict:
    """Period: avg lead days (scheduled_date - created_at) and reschedule stats
    over surgeries completed in range."""
    rows = _completed_in_range_q(db, date_from, date_to, facility, surgeon).all()
    n = len(rows)
    leads = [(s.scheduled_date - s.created_at.date()).days
             for s in rows if s.scheduled_date and s.created_at]
    resch = [int(s.reschedule_count or 0) for s in rows]
    avg_lead = round(sum(leads) / len(leads), 1) if leads else None
    rate = round(sum(1 for r in resch if r > 0) / n, 2) if n else 0.0
    avg_resch = round(sum(resch) / n, 2) if n else 0.0
    return {"n": n, "avg_lead_days": avg_lead,
            "reschedule_rate": rate, "avg_reschedules": avg_resch}


_BLOCKER_KEYS = ("benefits", "consents", "prior_auth", "clearance", "device", "labs")


def not_ready(db: Session, *, facility: Optional[str] = None,
              surgeon: Optional[str] = None, today: Optional[date] = None) -> dict:
    """Snapshot: surgeries scheduled in the next 14 days that are not fully
    ready, broken down by blocking step. A step blocks when its step-engine
    state is 'todo' or 'in_progress'."""
    from app.utils.dt import now_utc_naive
    today = today or now_utc_naive().date()
    horizon = today + timedelta(days=14)
    rows = (_base_query(db, facility, surgeon)
            .filter(Surgery.scheduled_date.isnot(None),
                    Surgery.scheduled_date >= today,
                    Surgery.scheduled_date <= horizon,
                    Surgery.status.notin_(("cancelled", "completed")))
            .all())
    by_blocker = {k: 0 for k in _BLOCKER_KEYS}
    total = 0
    for s in rows:
        blocked = [k for k in _BLOCKER_KEYS if _state(s, k) in ("todo", "in_progress")]
        if blocked:
            total += 1
            for k in blocked:
                by_blocker[k] += 1
    return {"total": total, "by_blocker": by_blocker}


def _stripe_only_predicates():
    """Restrict to genuine Stripe payments — mirrors surgery.py _stripe_only_filter.
    Manual offsets (kind='manual_offset') originate in ModMed and carry no Stripe
    id, so they're never in the posting backlog."""
    from sqlalchemy import or_
    from app.models.stripe_payment import SurgeryPayment
    return (
        SurgeryPayment.kind != "manual_offset",
        or_(SurgeryPayment.stripe_payment_intent_id.isnot(None),
            SurgeryPayment.stripe_checkout_session_id.isnot(None)),
    )


def posting_backlog(db: Session, *, facility: Optional[str] = None,
                    surgeon: Optional[str] = None) -> dict:
    """Snapshot: paid Stripe payments not yet posted to ModMed."""
    from app.models.stripe_payment import SurgeryPayment
    from app.utils.dt import now_utc_naive
    q = (db.query(SurgeryPayment)
         .outerjoin(Surgery, Surgery.id == SurgeryPayment.surgery_id)
         .filter(SurgeryPayment.status == "paid",
                 SurgeryPayment.posted_to_modmed_at.is_(None),
                 *_stripe_only_predicates()))
    if facility:
        q = q.filter(Surgery.selected_facility == facility)
    if surgeon:
        q = q.filter(Surgery.surgeon_primary == surgeon)
    rows = q.all()
    total = round(sum(float(p.amount_paid or 0) for p in rows), 2)
    paids = [p.paid_at for p in rows if p.paid_at]
    oldest_age = ((now_utc_naive() - min(paids)).days if paids else None)
    return {"count": len(rows), "total_amount": total, "oldest_age_days": oldest_age}


def _block_day_capacity(rule: dict) -> int:
    """Max cases a block day can hold: fixed-slot facilities use the slot count,
    option-based facilities use the largest option max."""
    if rule.get("slot_times"):
        return len(rule["slot_times"])
    opts = rule.get("options") or []
    return max((int(o.get("max", 0)) for o in opts), default=0)


def utilization(db: Session, *, date_from: date, date_to: date,
                facility: Optional[str] = None) -> dict:
    """Period: booked slots vs capacity across block days in range, per facility."""
    from app.models.surgery import BlockDay, SurgerySlot
    from app.services.surgery.block_schedule import capacity_rules
    rules = capacity_rules(db)
    q = db.query(BlockDay).filter(BlockDay.block_date >= date_from,
                                  BlockDay.block_date <= date_to)
    if facility:
        q = q.filter(BlockDay.facility == facility)
    by_fac: dict = {}
    for bd in q.all():
        booked = (db.query(func.count(SurgerySlot.id))
                  .filter(SurgerySlot.block_day_id == bd.id).scalar() or 0)
        cap = _block_day_capacity(rules.get(bd.facility) or {})
        agg = by_fac.setdefault(bd.facility, {"booked": 0, "capacity": 0})
        agg["booked"] += int(booked)
        agg["capacity"] += cap
    for fac, agg in by_fac.items():
        agg["pct"] = (round(agg["booked"] / agg["capacity"] * 100, 1)
                      if agg["capacity"] else 0.0)
    tot_b = sum(a["booked"] for a in by_fac.values())
    tot_c = sum(a["capacity"] for a in by_fac.values())
    return {"booked": tot_b, "capacity": tot_c,
            "overall_pct": round(tot_b / tot_c * 100, 1) if tot_c else 0.0,
            "by_facility": by_fac}


# ---------------------------------------------------------------------------
# Drill-down rows + CSV
# ---------------------------------------------------------------------------

# Frontend renders labels via STATUS_LABEL; keep a backend copy for CSV/rows.
STATUS_LABEL = {
    "incomplete": "Incomplete", "new": "New", "in_progress": "Benefits Check",
    "confirmed": "Pre-Surgery", "completed": "Post-Surgery", "hold": "Hold",
    "cancelled": "Canceled", "unresponsive": "Unresponsive",
}


def _surg_row(s) -> dict:
    return {
        "surgery_id": str(s.id),
        "surgery_number": s.surgery_number,
        "chart_number": s.chart_number,
        "patient_name": s.patient_name,
        "surgeon_primary": s.surgeon_primary,
        "selected_facility": s.selected_facility,
        "scheduled_date": s.scheduled_date.strftime("%m/%d/%Y") if s.scheduled_date else None,
        "status": s.status,
        "status_label": STATUS_LABEL.get(s.status, s.status),
    }


def rows_for(db: Session, tile: str, *, date_from: date, date_to: date,
             facility: Optional[str] = None, surgeon: Optional[str] = None,
             bucket: Optional[str] = None, today: Optional[date] = None) -> list[dict]:
    """Underlying rows for a clicked tile; `bucket` narrows to a sub-segment."""
    from app.utils.dt import now_utc_naive
    if tile == "status_funnel":
        q = _base_query(db, facility, surgeon)
        if bucket:
            q = q.filter(Surgery.status == bucket)
        return [_surg_row(s) for s in q.order_by(Surgery.scheduled_date).all()]

    if tile == "completed":
        rows = _completed_in_range_q(db, date_from, date_to, facility, surgeon).all()
        if bucket:
            rows = [s for s in rows if (s.procedure_classification or "unspecified") == bucket]
        out = []
        for s in rows:
            r = _surg_row(s)
            r["classification"] = s.procedure_classification
            r["completed_at"] = s.completed_at.strftime("%m/%d/%Y") if s.completed_at else None
            out.append(r)
        return out

    if tile == "cycle_time":
        out = []
        for s in _completed_in_range_q(db, date_from, date_to, facility, surgeon).all():
            r = _surg_row(s)
            r["lead_days"] = ((s.scheduled_date - s.created_at.date()).days
                              if s.scheduled_date and s.created_at else None)
            r["reschedule_count"] = int(s.reschedule_count or 0)
            out.append(r)
        return out

    if tile == "not_ready":
        today = today or now_utc_naive().date()
        horizon = today + timedelta(days=14)
        q = (_base_query(db, facility, surgeon)
             .filter(Surgery.scheduled_date.isnot(None),
                     Surgery.scheduled_date >= today,
                     Surgery.scheduled_date <= horizon,
                     Surgery.status.notin_(("cancelled", "completed"))))
        out = []
        for s in q.order_by(Surgery.scheduled_date).all():
            blockers = [k for k in _BLOCKER_KEYS if _state(s, k) in ("todo", "in_progress")]
            if not blockers:
                continue
            if bucket and bucket not in blockers:
                continue
            r = _surg_row(s)
            r["blockers"] = blockers
            out.append(r)
        return out

    if tile == "posting_backlog":
        from app.models.stripe_payment import SurgeryPayment
        q = (db.query(SurgeryPayment, Surgery)
             .outerjoin(Surgery, Surgery.id == SurgeryPayment.surgery_id)
             .filter(SurgeryPayment.status == "paid",
                     SurgeryPayment.posted_to_modmed_at.is_(None),
                     *_stripe_only_predicates()))
        if facility:
            q = q.filter(Surgery.selected_facility == facility)
        if surgeon:
            q = q.filter(Surgery.surgeon_primary == surgeon)
        out = []
        for p, s in q.all():
            out.append({
                "payment_id": str(p.id),
                "chart_number": getattr(s, "chart_number", None),
                "patient_name": getattr(s, "patient_name", None),
                "amount_paid": float(p.amount_paid or 0),
                "paid_at": p.paid_at.strftime("%m/%d/%Y") if p.paid_at else None,
                "confirmation": p.stripe_payment_intent_id or p.stripe_checkout_session_id,
            })
        return out

    if tile == "utilization":
        from app.models.surgery import BlockDay, SurgerySlot
        from app.services.surgery.block_schedule import capacity_rules
        rules = capacity_rules(db)
        q = db.query(BlockDay).filter(BlockDay.block_date >= date_from,
                                      BlockDay.block_date <= date_to)
        # `facility` is the filter-bar selection; `bucket` is the clicked facility
        # row in the utilization tile — both narrow to one facility.
        fac = bucket or facility
        if fac:
            q = q.filter(BlockDay.facility == fac)
        out = []
        for bd in q.order_by(BlockDay.block_date).all():
            booked = (db.query(func.count(SurgerySlot.id))
                      .filter(SurgerySlot.block_day_id == bd.id).scalar() or 0)
            out.append({
                "facility": bd.facility,
                "block_date": bd.block_date.strftime("%m/%d/%Y"),
                "block_kind": bd.block_kind,
                "booked": int(booked),
                "capacity": _block_day_capacity(rules.get(bd.facility) or {}),
            })
        return out

    raise ValueError(f"unknown tile: {tile}")


VALID_TILES = ("status_funnel", "not_ready", "completed", "cycle_time",
               "posting_backlog", "utilization")


def rows_to_csv(rows: list[dict]) -> str:
    """Serialize flat dict rows to CSV text. List-valued cells are joined with
    '; '. Empty input yields an empty string."""
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
