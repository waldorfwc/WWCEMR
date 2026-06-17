"""Surgery Reports aggregations. Each tile is a pure function over the Surgery
data, parameterized by an optional facility + surgeon filter and (for period
tiles) a date range. No persistence; computed on request."""
from __future__ import annotations

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
