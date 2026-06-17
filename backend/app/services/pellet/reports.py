"""Pellet Reports aggregations. Each tile is a pure function over the pellet
data, parameterized by an optional location + provider filter and (for period
tiles) a date range. No persistence; computed on request."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.pellet import PelletVisit


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
