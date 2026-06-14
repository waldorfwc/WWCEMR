"""Nightly sweep: cancel pellet visits that fell off the calendar.

A visit that's 7+ days past its scheduled date and still in a pre-insertion
state (status in {new, rescheduled} with at least one Proposed dose line) is
almost certainly a no-show or a cancellation that never got marked. These
visits accumulate indefinitely and block daily counts because inventory
thinks the planned/pulled doses are reserved.

This sweep:
  • Picks visits where scheduled_date <= today - STALE_DAYS, status in
    {new, rescheduled, in_progress}, with ≥1 proposed dose.
  • Returns any 'pulled' doses to stock at the visit's location (FIFO didn't
    move from the original location).
  • Marks every 'planned' or 'pulled' dose as 'returned'.
  • Sets the visit status to 'cancelled' and outcome to 'cancelled'.
  • Writes an audit row per visit ('visit_auto_cancelled').

Safe to re-run; idempotent — once cancelled, the visit no longer matches.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from app.utils.dt import now_utc_naive

from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models.pellet import (
    PelletAuditEvent, PelletStock, PelletVisit, PelletVisitDose,
)
from app.services.pellet.settings import cfg


STALE_DAYS = 7


def _audit_row(db: Session, **kwargs) -> PelletAuditEvent:
    e = PelletAuditEvent(**kwargs)
    db.add(e)
    return e


def _get_or_create_stock(db: Session, lot_id, location: str) -> PelletStock:
    s = (db.query(PelletStock)
           .filter(PelletStock.lot_id == lot_id,
                   PelletStock.location == location).first())
    if s:
        return s
    s = PelletStock(lot_id=lot_id, location=location, doses_on_hand=0)
    db.add(s); db.flush()
    return s


def sweep_stale_visits(db: Session, *,
                        as_of: date | None = None,
                        actor: str = "system:cron") -> dict:
    """Run the sweep. Returns counts of visits / doses affected."""
    today = as_of or date.today()
    cutoff = today - timedelta(days=cfg(db, "stale_visit_days"))

    candidates = (db.query(PelletVisit)
                    .options(joinedload(PelletVisit.doses)
                                .joinedload(PelletVisitDose.dose_type),
                              joinedload(PelletVisit.patient))
                    .join(PelletVisitDose, PelletVisitDose.visit_id == PelletVisit.id)
                    .filter(PelletVisit.scheduled_date.isnot(None),
                            PelletVisit.scheduled_date <= cutoff,
                            PelletVisit.status.in_(["new", "rescheduled", "in_progress"]),
                            PelletVisitDose.status.in_(["planned", "pulled"]))
                    .distinct().all())

    visits_cancelled = 0
    doses_returned = 0
    stock_returned = 0
    skipped_no_location = 0
    now = now_utc_naive()

    PELLET_LOCATIONS = ("white_plains", "brandywine", "arlington")

    for v in candidates:
        location = v.location if v.location in PELLET_LOCATIONS else None
        proposed_doses = [d for d in (v.doses or []) if d.status in ("planned", "pulled")]
        # If any of the proposed doses actually decremented stock (status='pulled'
        # with a lot), we MUST know which location to return them to. Skip and
        # surface so an admin can fix it manually.
        needs_stock_return = any(d.status == "pulled" and d.lot_id for d in proposed_doses)
        if needs_stock_return and location is None:
            skipped_no_location += 1
            import logging
            logging.getLogger(__name__).warning(
                "pellet_stale_sweep: skipping visit %s — no location set and has "
                "pulled doses (manual intervention required)", v.id)
            continue
        for d in proposed_doses:
            # Pulled doses had stock decremented; return to the visit's location.
            if d.status == "pulled" and d.lot_id:
                stock = _get_or_create_stock(db, d.lot_id, location)
                stock.doses_on_hand += d.quantity
                stock_returned += d.quantity
                _audit_row(db, actor=actor, action="dose_proposed_return",
                            lot_id=d.lot_id, location=location,
                            delta_doses=d.quantity,
                            summary=(f"Auto-return {d.quantity} doses to {location} "
                                    f"(visit {v.id} stale {(today - v.scheduled_date).days}d)"),
                            detail={"visit_id": str(v.id), "visit_dose_id": str(d.id),
                                    "reason": "stale_auto_cancel"})
            d.status = "returned"
            d.resolved_at = now
            d.resolved_by = actor
            doses_returned += 1

        v.status = "cancelled"
        v.outcome = "cancelled"
        v.outcome_notes = (v.outcome_notes or "") + \
            ("\n" if v.outcome_notes else "") + \
            f"Auto-cancelled by system sweep on {today.isoformat()} " \
            f"({(today - v.scheduled_date).days}d past scheduled date, " \
            f"{len(proposed_doses)} unconfirmed dose(s))."
        _audit_row(db, actor=actor, action="visit_auto_cancelled",
                    summary=(f"Auto-cancelled visit "
                            + (v.patient.patient_name if v.patient else str(v.id))
                            + f" — {(today - v.scheduled_date).days}d stale, "
                            + f"{len(proposed_doses)} dose(s) returned"),
                    detail={"visit_id": str(v.id), "patient_id": str(v.patient_id),
                            "scheduled_date": str(v.scheduled_date),
                            "days_stale": (today - v.scheduled_date).days,
                            "doses_returned": len(proposed_doses)})
        visits_cancelled += 1

    if visits_cancelled:
        db.commit()
    return {"visits_cancelled":      visits_cancelled,
            "doses_returned":        doses_returned,
            "stock_returned":        stock_returned,
            "skipped_no_location":   skipped_no_location,
            "as_of":                 str(today)}


def run_sweep_job() -> None:
    """Entry point for the APScheduler nightly job."""
    db = SessionLocal()
    try:
        result = sweep_stale_visits(db)
        if result["visits_cancelled"]:
            import logging
            logging.getLogger(__name__).info(
                "pellet_stale_sweep: cancelled %d visits, returned %d doses (%d stock)",
                result["visits_cancelled"], result["doses_returned"], result["stock_returned"])
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("pellet_stale_sweep error: %s", exc)
    finally:
        db.close()
