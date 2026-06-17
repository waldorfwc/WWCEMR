"""Pellet Reports aggregations. Each tile is a pure function over the pellet
data, parameterized by an optional location + provider filter and (for period
tiles) a date range. No persistence; computed on request."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.pellet import PelletPatient, PelletVisit
from app.models.pellet_portal import PelletConsent


def _visit_base(db: Session, location: Optional[str], provider: Optional[str]):
    """PelletVisit query excluding historical-import rows, with optional
    location/provider filters."""
    q = db.query(PelletVisit).filter(PelletVisit.is_historical.is_(False))
    if location:
        q = q.filter(PelletVisit.location == location)
    if provider:
        q = q.filter(PelletVisit.provider == provider)
    return q


def _dt_floor(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _inserted_in_range_q(db, date_from, date_to, location, provider):
    return (_visit_base(db, location, provider)
            .filter(PelletVisit.status.in_(("inserted", "billed")),
                    PelletVisit.inserted_at.isnot(None),
                    PelletVisit.inserted_at >= _dt_floor(date_from),
                    PelletVisit.inserted_at < _dt_floor(date_to + timedelta(days=1))))


def status_funnel(db: Session, *, location: Optional[str] = None,
                  provider: Optional[str] = None) -> dict:
    rows = (_visit_base(db, location, provider)
            .with_entities(PelletVisit.status, func.count(PelletVisit.id))
            .group_by(PelletVisit.status).all())
    return {"by_status": {status: int(n) for status, n in rows}}


def insertions(db: Session, *, date_from: date, date_to: date,
               location: Optional[str] = None, provider: Optional[str] = None) -> dict:
    rows = (_inserted_in_range_q(db, date_from, date_to, location, provider)
            .with_entities(PelletVisit.visit_kind, func.count(PelletVisit.id))
            .group_by(PelletVisit.visit_kind).all())
    by_kind = {(k or "unspecified"): int(n) for k, n in rows}
    total = sum(by_kind.values())
    length = (date_to - date_from).days + 1
    prior_to = date_from - timedelta(days=1)
    prior_from = prior_to - timedelta(days=length - 1)
    prior_total = _inserted_in_range_q(db, prior_from, prior_to, location, provider).count()
    return {"total": total, "by_kind": by_kind, "prior_total": prior_total,
            "prior_from": prior_from, "prior_to": prior_to, "delta": total - prior_total}


def providers(db: Session) -> list[str]:
    """Distinct non-null providers across non-historical visits (for the filter
    dropdown — independent of the date range)."""
    rows = (db.query(PelletVisit.provider)
            .filter(PelletVisit.is_historical.is_(False),
                    PelletVisit.provider.isnot(None))
            .distinct().all())
    return sorted({r[0] for r in rows if r[0]})


def _effective_visit_date(v) -> Optional[date]:
    if v.inserted_at:
        return v.inserted_at.date()
    return v.scheduled_date


def _has_open_visit(visits) -> bool:
    return any(v.status not in ("billed", "cancelled") for v in visits)


def recall_due(db: Session, *, location: Optional[str] = None,
               provider: Optional[str] = None, today: Optional[date] = None) -> dict:
    """Snapshot: active patients past (or nearing) their recall interval, with
    no open visit. Mirrors pellet.py recall_is_due (interval*30 days)."""
    from app.utils.dt import now_utc_naive
    today = today or now_utc_naive().date()
    soon = today + timedelta(days=30)
    overdue = due_soon = 0
    patients = (db.query(PelletPatient)
                .filter(PelletPatient.status == "active").all())
    for p in patients:
        visits = list(p.visits or [])
        if not visits:
            continue
        latest = max(visits, key=lambda v: (v.created_at or datetime.min))
        if location and latest.location != location:
            continue
        if provider and latest.provider != provider:
            continue
        dates = [d for d in (_effective_visit_date(v) for v in visits) if d]
        if not dates:
            continue
        last_dt = max(dates)
        interval = p.recall_interval_months or 4
        due = last_dt + timedelta(days=interval * 30)
        if _has_open_visit(visits):
            continue
        if due < today:
            overdue += 1
        elif due <= soon:
            due_soon += 1
    return {"overdue": overdue, "due_soon": due_soon, "total": overdue + due_soon}


def _mammo_ok(db, p, today) -> bool:
    from app.services.pellet.settings import cfg
    if not cfg(db, "require_mammo"):
        return True
    if not (p.mammo_verified and p.mammo_date):
        return False
    return (today - p.mammo_date).days <= int(cfg(db, "mammo_valid_days"))


def _labs_ok(db, p, today) -> bool:
    from app.services.pellet.settings import cfg
    if not cfg(db, "require_labs") or p.labs_not_required:
        return True
    if not (p.labs_verified and p.labs_date):
        return False
    return (today - p.labs_date).days <= int(cfg(db, "labs_valid_days"))


def _consent_ok(db, patient_id) -> bool:
    rows = (db.query(PelletConsent)
            .filter(PelletConsent.pellet_patient_id == patient_id).all())
    return any(c.is_valid for c in rows)


def prerequisites(db: Session, *, location: Optional[str] = None,
                  provider: Optional[str] = None, today: Optional[date] = None) -> dict:
    """Snapshot: upcoming visits (scheduled <=14 days, status new/in_progress)
    whose patient is missing mammo / labs / consent."""
    from app.utils.dt import now_utc_naive
    today = today or now_utc_naive().date()
    horizon = today + timedelta(days=14)
    rows = (_visit_base(db, location, provider)
            .filter(PelletVisit.scheduled_date.isnot(None),
                    PelletVisit.scheduled_date >= today,
                    PelletVisit.scheduled_date <= horizon,
                    PelletVisit.status.in_(("new", "in_progress")))
            .all())
    by_blocker = {"mammo": 0, "labs": 0, "consent": 0}
    total = 0
    for v in rows:
        p = v.patient
        if p is None:
            continue
        blockers = []
        if not _mammo_ok(db, p, today):
            blockers.append("mammo")
        if not _labs_ok(db, p, today):
            blockers.append("labs")
        if not _consent_ok(db, p.id):
            blockers.append("consent")
        if blockers:
            total += 1
            for b in blockers:
                by_blocker[b] += 1
    return {"total": total, "by_blocker": by_blocker}
