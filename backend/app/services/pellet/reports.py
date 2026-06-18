"""Pellet Reports aggregations. Each tile is a pure function over the pellet
data, parameterized by an optional location + provider filter and (for period
tiles) a date range. No persistence; computed on request."""
from __future__ import annotations

import csv
import io
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


def _mammo_ok(db, p, ref: date) -> bool:
    """Mammo readiness as of the visit's `ref` date. Mirrors pellet.py
    _mammo_status staleness rule: stale when mammo_date < ref - N days."""
    from app.services.pellet.settings import cfg
    if not cfg(db, "require_mammo"):
        return True
    if not (p.mammo_verified and p.mammo_date):
        return False
    return p.mammo_date >= ref - timedelta(days=int(cfg(db, "mammo_valid_days")))


def _labs_ok(db, p, ref: date) -> bool:
    """Labs readiness as of the visit's `ref` date. Mirrors pellet.py
    _labs_status staleness rule: stale when labs_date < ref - N days."""
    from app.services.pellet.settings import cfg
    if not cfg(db, "require_labs") or p.labs_not_required:
        return True
    if not (p.labs_verified and p.labs_date):
        return False
    return p.labs_date >= ref - timedelta(days=int(cfg(db, "labs_valid_days")))


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
        ref = v.scheduled_date or today
        blockers = []
        if not _mammo_ok(db, p, ref):
            blockers.append("mammo")
        if not _labs_ok(db, p, ref):
            blockers.append("labs")
        if not _consent_ok(db, p.id):
            blockers.append("consent")
        if blockers:
            total += 1
            for b in blockers:
                by_blocker[b] += 1
    return {"total": total, "by_blocker": by_blocker}


def billing_backlog(db: Session, *, location: Optional[str] = None,
                    provider: Optional[str] = None) -> dict:
    """Snapshot: inserted visits not yet billed."""
    rows = (_visit_base(db, location, provider)
            .filter(PelletVisit.status == "inserted",
                    PelletVisit.billed_at.is_(None)).all())
    total = round(sum(float(v.price_amount or 0) for v in rows), 2)
    return {"count": len(rows), "total_amount": total}


def inventory_health(db: Session, *, location: Optional[str] = None,
                     today: Optional[date] = None) -> dict:
    """Snapshot: on-hand doses per location, lots expiring <=90 days, and dose
    types below their reorder threshold."""
    from app.models.pellet import PelletDoseType, PelletLot, PelletStock
    from app.utils.dt import now_utc_naive
    today = today or now_utc_naive().date()
    horizon = today + timedelta(days=90)

    sq = db.query(PelletStock).filter(PelletStock.status == "active")
    if location:
        sq = sq.filter(PelletStock.location == location)
    stocks = sq.all()

    by_location: dict = {}
    expiring_lot_ids: set = set()
    onhand_by_loc_type: dict = {}   # (location, dose_type_id) -> doses
    lots = {l.id: l for l in db.query(PelletLot).all()}
    for s in stocks:
        by_location[s.location] = by_location.get(s.location, 0) + int(s.doses_on_hand or 0)
        lot = lots.get(s.lot_id)
        if lot and (s.doses_on_hand or 0) > 0 and lot.expiration_date <= horizon:
            expiring_lot_ids.add(s.lot_id)
        if lot:
            key = (s.location, lot.dose_type_id)
            onhand_by_loc_type[key] = onhand_by_loc_type.get(key, 0) + int(s.doses_on_hand or 0)

    below = 0
    dose_types = {d.id: d for d in db.query(PelletDoseType).all()}
    for d in dose_types.values():
        thresholds = d.reorder_thresholds_by_location or {}
        for loc, thr in thresholds.items():
            if location and loc != location:
                continue
            if thr is None:
                continue
            on_hand = onhand_by_loc_type.get((loc, d.id), 0)
            if on_hand < int(thr):
                below += 1

    return {"total_on_hand": sum(by_location.values()),
            "by_location": by_location,
            "expiring_lots": len(expiring_lot_ids),
            "below_reorder": below}


def _visit_row(v) -> dict:
    p = v.patient
    return {
        "visit_id": str(v.id),
        "chart_number": getattr(p, "chart_number", None),
        "patient_name": getattr(p, "patient_name", None),
        "scheduled_date": v.scheduled_date.strftime("%m/%d/%Y") if v.scheduled_date else None,
        "inserted_at": v.inserted_at.strftime("%m/%d/%Y") if v.inserted_at else None,
        "location": v.location,
        "provider": v.provider,
        "status": v.status,
        "visit_kind": v.visit_kind,
    }


def rows_for(db: Session, tile: str, *, date_from: date, date_to: date,
             location: Optional[str] = None, provider: Optional[str] = None,
             bucket: Optional[str] = None, today: Optional[date] = None) -> list[dict]:
    from app.utils.dt import now_utc_naive

    if tile == "status_funnel":
        q = _visit_base(db, location, provider)
        if bucket:
            q = q.filter(PelletVisit.status == bucket)
        return [_visit_row(v) for v in q.order_by(PelletVisit.scheduled_date).all()]

    if tile == "insertions":
        rows = _inserted_in_range_q(db, date_from, date_to, location, provider).all()
        if bucket:
            rows = [v for v in rows if (v.visit_kind or "unspecified") == bucket]
        out = []
        for v in rows:
            r = _visit_row(v)
            r["price_amount"] = float(v.price_amount or 0)
            out.append(r)
        return out

    if tile == "billing_backlog":
        rows = (_visit_base(db, location, provider)
                .filter(PelletVisit.status == "inserted",
                        PelletVisit.billed_at.is_(None)).all())
        out = []
        for v in rows:
            r = _visit_row(v)
            r["price_amount"] = float(v.price_amount or 0)
            out.append(r)
        return out

    if tile == "prerequisites":
        today = today or now_utc_naive().date()
        horizon = today + timedelta(days=14)
        q = (_visit_base(db, location, provider)
             .filter(PelletVisit.scheduled_date.isnot(None),
                     PelletVisit.scheduled_date >= today,
                     PelletVisit.scheduled_date <= horizon,
                     PelletVisit.status.in_(("new", "in_progress"))))
        out = []
        for v in q.order_by(PelletVisit.scheduled_date).all():
            p = v.patient
            if p is None:
                continue
            ref = v.scheduled_date or today
            blockers = []
            if not _mammo_ok(db, p, ref):
                blockers.append("mammo")
            if not _labs_ok(db, p, ref):
                blockers.append("labs")
            if not _consent_ok(db, p.id):
                blockers.append("consent")
            if not blockers:
                continue
            if bucket and bucket not in blockers:
                continue
            r = _visit_row(v)
            r["blockers"] = blockers
            out.append(r)
        return out

    if tile == "recall_due":
        today = today or now_utc_naive().date()
        soon = today + timedelta(days=30)
        out = []
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
            if not dates or _has_open_visit(visits):
                continue
            last_dt = max(dates)
            interval = p.recall_interval_months or 4
            due = last_dt + timedelta(days=interval * 30)
            kind = "overdue" if due < today else ("due_soon" if due <= soon else None)
            if kind is None:
                continue
            if bucket and bucket != kind:
                continue
            out.append({
                "patient_id": str(p.id),
                "chart_number": p.chart_number,
                "patient_name": p.patient_name,
                "last_inserted_at": last_dt.strftime("%m/%d/%Y"),
                "due_date": due.strftime("%m/%d/%Y"),
                "recall_interval_months": p.recall_interval_months or 4,
                "bucket": kind,
            })
        return out

    if tile == "inventory_health":
        from app.models.pellet import PelletDoseType, PelletLot, PelletStock
        today = today or now_utc_naive().date()
        horizon = today + timedelta(days=90)
        types = {d.id: d for d in db.query(PelletDoseType).all()}

        if bucket == "below_reorder":
            # Synthetic rows per (location, dose_type) below its reorder
            # threshold — on-hand summed over ALL active stock (incl. zero/no
            # stock), so the drill matches the headline count exactly and the
            # "we're out" cases (the most important ones) actually show up.
            stock_q = db.query(PelletStock).filter(PelletStock.status == "active")
            if location:
                stock_q = stock_q.filter(PelletStock.location == location)
            lots = {l.id: l for l in db.query(PelletLot).all()}
            onhand: dict = {}
            for s in stock_q.all():
                lot = lots.get(s.lot_id)
                if lot:
                    k = (s.location, lot.dose_type_id)
                    onhand[k] = onhand.get(k, 0) + int(s.doses_on_hand or 0)
            out = []
            for d in types.values():
                for loc, thr in (d.reorder_thresholds_by_location or {}).items():
                    if thr is None or (location and loc != location):
                        continue
                    on = onhand.get((loc, d.id), 0)
                    if on < int(thr):
                        out.append({"location": loc, "dose_type": d.label,
                                    "doses_on_hand": on, "reorder_threshold": int(thr)})
            return out

        # Default / location / expiring: one row per in-stock lot×location.
        sq = db.query(PelletStock).filter(PelletStock.status == "active",
                                          PelletStock.doses_on_hand > 0)
        if location:
            sq = sq.filter(PelletStock.location == location)
        # Any bucket that isn't the "expiring" pseudo-bucket is a location.
        if bucket and bucket != "expiring":
            sq = sq.filter(PelletStock.location == bucket)
        lots = {l.id: l for l in db.query(PelletLot).all()}
        out = []
        for s in sq.all():
            lot = lots.get(s.lot_id)
            dose = types.get(lot.dose_type_id) if lot else None
            if bucket == "expiring":
                if not (lot and lot.expiration_date and lot.expiration_date <= horizon):
                    continue
            out.append({
                "location": s.location,
                "lot_number": lot.qualgen_lot_number if lot else None,
                "dose_type": (dose.label if dose else None),
                "doses_on_hand": int(s.doses_on_hand or 0),
                "expiration_date": (lot.expiration_date.strftime("%m/%d/%Y")
                                    if lot and lot.expiration_date else None),
            })
        return out

    raise ValueError(f"unknown tile: {tile}")


VALID_TILES = ("status_funnel", "insertions", "recall_due", "prerequisites",
               "billing_backlog", "inventory_health")


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
