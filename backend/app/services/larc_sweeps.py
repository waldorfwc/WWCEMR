"""Scheduled sweeps for the LARC module — run by APScheduler or manually
via the admin endpoints.

Three sweeps:

1. **Expiry hold** — devices with <365 days to expiry get pulled off any
   active assignment and moved to 'unassigned' status. The patient (if
   any) lands on the Owed list with `expires_at` set to the device's
   expiration_date.

2. **Reallocate stale assignments** — assignments that haven't been
   inserted within 180 days of creation get their device freed; the
   patient goes on the Owed list.

3. **Pharmacy SLA follow-up** — pharmacy orders faxed >14 days ago with
   no device received yet are flagged for staff follow-up (writes an
   audit row; the dashboard already surfaces them).

Each sweep is idempotent and safe to run repeatedly.
"""
from __future__ import annotations

from datetime import date as _date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models.larc import (
    LarcAssignment, LarcAuditEvent, LarcDevice, LarcOwedPatient,
)
from app.services.larc_workflow import (
    ASSIGNMENT_REALLOCATE_AFTER_DAYS, DEVICE_EXPIRY_HOLD_DAYS,
    PHARMACY_ORDER_SLA_DAYS, log_audit,
)


def _push_to_owed(db: Session, a: LarcAssignment, expires_at: Optional[_date],
                    actor: str, summary: str) -> None:
    """Helper — move an assignment's patient to the Owed list and
    deactivate the assignment + free the device."""
    # Don't double-add to Owed (idempotent on chart + original assignment)
    existing = (db.query(LarcOwedPatient)
                  .filter(LarcOwedPatient.chart_number == a.chart_number,
                          LarcOwedPatient.original_assignment_id == a.id,
                          LarcOwedPatient.resolved_at.is_(None))
                  .first())
    if existing:
        return
    if not a.device:
        return
    owed = LarcOwedPatient(
        chart_number=a.chart_number,
        patient_name=a.patient_name,
        original_assignment_id=a.id,
        original_device_type_id=a.device.device_type_id,
        expires_at=expires_at,
    )
    db.add(owed)
    a.is_active = False
    a.status = "owed"
    a.device.status = "unassigned"
    log_audit(db, actor=actor, action="device_reallocated",
              device=a.device, assignment=a,
              summary=summary,
              detail={"expires_at": str(expires_at) if expires_at else None})


def sweep_expiry_hold(db: Session, *, today: Optional[_date] = None) -> dict:
    """Devices expiring within DEVICE_EXPIRY_HOLD_DAYS go back to unassigned.
    Patients on those assignments land on the Owed list."""
    today = today or _date.today()
    horizon = today + timedelta(days=DEVICE_EXPIRY_HOLD_DAYS)
    n_reallocated = 0
    candidates = (db.query(LarcDevice)
                    .filter(LarcDevice.expiration_date.isnot(None),
                            LarcDevice.expiration_date <= horizon,
                            LarcDevice.status == "assigned")
                    .all())
    for d in candidates:
        active = (db.query(LarcAssignment)
                    .options(joinedload(LarcAssignment.device))
                    .filter(LarcAssignment.device_id == d.id,
                            LarcAssignment.is_active.is_(True))
                    .first())
        if not active:
            d.status = "unassigned"
            continue
        _push_to_owed(db, active, expires_at=d.expiration_date,
                       actor="system:expiry-sweep",
                       summary=(f"Reallocated {d.our_id} (expires {d.expiration_date}) — "
                                f"{active.patient_name} moved to Owed list"))
        n_reallocated += 1
    db.commit()
    return {"reallocated_for_expiry": n_reallocated}


def sweep_stale_assignments(db: Session, *, today: Optional[_date] = None) -> dict:
    """Assignments older than ASSIGNMENT_REALLOCATE_AFTER_DAYS with no
    insertion yet → patient goes on Owed list."""
    today = today or _date.today()
    cutoff = datetime.utcnow() - timedelta(days=ASSIGNMENT_REALLOCATE_AFTER_DAYS)
    n_reallocated = 0
    candidates = (db.query(LarcAssignment)
                    .options(joinedload(LarcAssignment.device))
                    .filter(LarcAssignment.is_active.is_(True),
                            LarcAssignment.created_at <= cutoff,
                            LarcAssignment.inserted_at.is_(None),
                            LarcAssignment.status.notin_(["billed", "cancelled"]))
                    .all())
    for a in candidates:
        if not a.device:
            continue
        # Owed-list expiration: until original device's expiration_date
        _push_to_owed(db, a, expires_at=a.device.expiration_date,
                       actor="system:stale-sweep",
                       summary=(f"Reallocated device #{a.device.our_id} — assignment unused for >"
                                f"{ASSIGNMENT_REALLOCATE_AFTER_DAYS} days; "
                                f"{a.patient_name} moved to Owed list"))
        n_reallocated += 1
    db.commit()
    return {"reallocated_stale": n_reallocated}


def sweep_pharmacy_sla(db: Session) -> dict:
    """Pharmacy orders faxed >SLA days ago with no device received → write
    audit row. The dashboard already surfaces them in real-time; this
    sweep adds a daily audit timestamp so the breach is on record."""
    cutoff = datetime.utcnow() - timedelta(days=PHARMACY_ORDER_SLA_DAYS)
    rows = (db.query(LarcAssignment)
              .options(joinedload(LarcAssignment.device))
              .filter(LarcAssignment.source_flow == "pharmacy_order",
                      LarcAssignment.request_faxed_at.isnot(None),
                      LarcAssignment.request_faxed_at <= cutoff,
                      LarcAssignment.device_received_at.is_(None))
              .all())
    for a in rows:
        days_overdue = (datetime.utcnow() - a.request_faxed_at).days - PHARMACY_ORDER_SLA_DAYS
        # Only write once per assignment per day (idempotent-ish)
        recent = (db.query(LarcAuditEvent)
                    .filter(LarcAuditEvent.assignment_id == a.id,
                            LarcAuditEvent.action == "pharmacy_sla_breach",
                            LarcAuditEvent.occurred_at >= datetime.utcnow() - timedelta(hours=20))
                    .first())
        if recent:
            continue
        log_audit(db, actor="system:pharmacy-sla", action="pharmacy_sla_breach",
                  device=a.device, assignment=a,
                  summary=f"Pharmacy order for {a.patient_name} is {days_overdue}d past 14-day SLA",
                  detail={"days_overdue": days_overdue,
                           "faxed_at": a.request_faxed_at.isoformat()})
    db.commit()
    return {"sla_alerts_logged": len(rows)}


def run_all() -> dict:
    """Run all three sweeps in one transaction-safe pass."""
    db = SessionLocal()
    try:
        out = {}
        out.update(sweep_expiry_hold(db))
        out.update(sweep_stale_assignments(db))
        out.update(sweep_pharmacy_sla(db))
        return out
    finally:
        db.close()
