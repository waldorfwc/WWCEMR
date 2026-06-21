"""Scheduled sweeps for the LARC module — run by APScheduler or manually
via the admin endpoints.

Three sweeps:

1. **Expiry hold** — devices with <365 days to expiry get pulled off any
   active assignment and moved to 'unassigned' status. The patient (if
   any) lands on the Owed list with `expires_at` set to the device's
   expiration_date.

2. **Reallocate stale assignments** — assignments not inserted within
   180 days of device receipt (falling back to creation date when no
   receipt is recorded) get their device freed; the patient goes on the
   Owed list, and a patient-owned device is auto-claimed as WWC Claimed.

3. **Pharmacy SLA follow-up** — pharmacy orders faxed >14 days ago with
   no device received yet are flagged for staff follow-up (writes an
   audit row; the dashboard already surfaces them).

Each sweep is idempotent and safe to run repeatedly.
"""
from __future__ import annotations

from datetime import date as _date, datetime, timedelta
from app.utils.dt import now_utc_naive
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models.larc import (
    LarcAssignment, LarcAuditEvent, LarcDevice, LarcOwedPatient,
)
from app.services.larc.workflow import (
    ASSIGNMENT_REALLOCATE_AFTER_DAYS, DEVICE_EXPIRY_HOLD_DAYS,
    PHARMACY_ORDER_SLA_DAYS, log_audit,
)
from app.services.larc.settings import cfg


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
    # Auto-claim: a patient-owned device pulled back to the Owed list is
    # now WWC's to bill. (wwc_owned / wwc_claimed are left as-is.)
    if a.device.ownership == "patient_owned":
        a.device.ownership = "wwc_claimed"
        log_audit(db, actor=actor, action="ownership_changed",
                  device=a.device, assignment=a,
                  summary=("Ownership changed: patient owned → wwc claimed. "
                           f"Reason: auto-claimed on reallocation ({actor})."),
                  detail={"from": "patient_owned",
                          "to": "wwc_claimed",
                          "reason": f"auto-claimed on reallocation ({actor})"})


def sweep_expiry_hold(db: Session, *, today: Optional[_date] = None) -> dict:
    """Devices expiring within DEVICE_EXPIRY_HOLD_DAYS go back to unassigned.
    Patients on those assignments land on the Owed list."""
    today = today or _date.today()
    horizon = today + timedelta(days=cfg(db, "device_expiry_hold_days"))
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
    reallocate_after_days = cfg(db, "assignment_reallocate_after_days")
    cutoff = now_utc_naive() - timedelta(days=reallocate_after_days)
    n_reallocated = 0
    candidates = (db.query(LarcAssignment)
                    .options(joinedload(LarcAssignment.device))
                    .filter(LarcAssignment.is_active.is_(True),
                            func.coalesce(LarcAssignment.device_received_at,
                                          LarcAssignment.created_at) <= cutoff,
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
                                f"{reallocate_after_days} days; "
                                f"{a.patient_name} moved to Owed list"))
        n_reallocated += 1
    db.commit()
    return {"reallocated_stale": n_reallocated}


def sweep_pharmacy_sla(db: Session) -> dict:
    """Pharmacy orders faxed >SLA days ago with no device received → write
    audit row. The dashboard already surfaces them in real-time; this
    sweep adds a daily audit timestamp so the breach is on record."""
    sla_days = cfg(db, "pharmacy_order_sla_days")
    cutoff = now_utc_naive() - timedelta(days=sla_days)
    rows = (db.query(LarcAssignment)
              .options(joinedload(LarcAssignment.device))
              .filter(LarcAssignment.source_flow == "pharmacy_order",
                      LarcAssignment.request_faxed_at.isnot(None),
                      LarcAssignment.request_faxed_at <= cutoff,
                      LarcAssignment.device_received_at.is_(None))
              .all())
    for a in rows:
        days_overdue = (now_utc_naive() - a.request_faxed_at).days - sla_days
        # Only write once per assignment per day (idempotent-ish)
        recent = (db.query(LarcAuditEvent)
                    .filter(LarcAuditEvent.assignment_id == a.id,
                            LarcAuditEvent.action == "pharmacy_sla_breach",
                            LarcAuditEvent.occurred_at >= now_utc_naive() - timedelta(hours=20))
                    .first())
        if recent:
            continue
        log_audit(db, actor="system:pharmacy-sla", action="pharmacy_sla_breach",
                  device=a.device, assignment=a,
                  summary=f"Pharmacy order for {a.patient_name} is {days_overdue}d past {sla_days}-day SLA",
                  detail={"days_overdue": days_overdue,
                           "faxed_at": a.request_faxed_at.isoformat()})
    db.commit()
    return {"sla_alerts_logged": len(rows)}


def sweep_fax_retry(db: Session) -> dict:
    """Retry envelopes whose last auto-fax attempt failed and whose
    next_fax_retry_at has come due. Concurrency-safe: each row is
    claimed via the same conditional-UPDATE pattern as the webhook
    handler (fix #2). Envelopes in a terminal state (voided/declined/
    revoked/expired) or already terminally failed are skipped.

    Returns a counter dict: {fax_retry_attempted, fax_retry_succeeded,
    fax_retry_failed, fax_retry_terminal}.
    """
    from sqlalchemy import or_
    from app.models.larc import LarcEnrollmentEnvelope
    from app.services.larc.pharmacy_fax import fax_envelope

    now = now_utc_naive()
    candidates = (db.query(LarcEnrollmentEnvelope)
                    .filter(LarcEnrollmentEnvelope.fax_status == "fax_failed",
                            LarcEnrollmentEnvelope.next_fax_retry_at.isnot(None),
                            LarcEnrollmentEnvelope.next_fax_retry_at <= now,
                            LarcEnrollmentEnvelope.fax_terminally_failed_at.is_(None),
                            LarcEnrollmentEnvelope.status.notin_(
                                ("declined", "voided", "revoked", "expired", "faxed")))
                    .all())

    attempted = 0
    succeeded = 0
    failed = 0
    terminal = 0
    for env in candidates:
        # Atomic claim — flip fax_status to in_progress only if it is
        # still fax_failed AND the retry slot is still due. Two
        # concurrent sweep workers both see the row, both run this
        # UPDATE, exactly one wins.
        claimed = db.query(LarcEnrollmentEnvelope).filter(
            LarcEnrollmentEnvelope.id == env.id,
            LarcEnrollmentEnvelope.fax_status == "fax_failed",
            LarcEnrollmentEnvelope.next_fax_retry_at.isnot(None),
            LarcEnrollmentEnvelope.next_fax_retry_at <= now,
        ).update(
            {LarcEnrollmentEnvelope.fax_status: "in_progress"},
            synchronize_session=False,
        )
        if claimed == 0:
            continue   # another worker took it
        db.refresh(env)
        attempted += 1
        try:
            result = fax_envelope(db, env, by_email="system:retry-sweep",
                                    force=True)
        except Exception as exc:
            # fax_envelope's own internal failures are already audited;
            # any wrapper-level exception is a code bug — log and move on
            # so one bad row doesn't poison the rest of the sweep.
            log_audit_failure_internal_only(exc)
            failed += 1
            continue
        if result.get("ok"):
            succeeded += 1
        else:
            failed += 1
            if result.get("terminal"):
                terminal += 1

    return {
        "fax_retry_attempted": attempted,
        "fax_retry_succeeded": succeeded,
        "fax_retry_failed":    failed,
        "fax_retry_terminal":  terminal,
    }


def log_audit_failure_internal_only(exc):
    import logging
    logging.getLogger(__name__).exception(
        "sweep_fax_retry: unhandled exception in fax_envelope wrapper: %s",
        exc)


def sweep_unwedge_fax_in_progress(db: Session, *,
                                    max_age_minutes: int = 15) -> dict:
    """Reset envelopes whose fax_status='in_progress' is older than
    max_age_minutes — they've wedged.

    apply_webhook_event flips fax_status to 'in_progress' before calling
    fax_envelope. If the Cloud Run instance is killed (OOM, deploy,
    scale-in) between the claim and the fax call, the row stays
    'in_progress' forever: sweep_fax_retry excludes it (only resets
    'fax_failed'), and BoldSign webhook redeliveries are filtered out by
    the in_progress guard. The pharmacy order silently never goes out.
    (Fable LARC audit H3.)

    Resets last_fax_error so the operator dashboard can see why we
    reset, then flips fax_status back to 'fax_failed' with
    next_fax_retry_at=now so sweep_fax_retry picks it up on its next
    cycle.
    """
    from app.models.larc import LarcEnrollmentEnvelope
    now = now_utc_naive()
    cutoff = now - timedelta(minutes=max_age_minutes)
    wedged = (db.query(LarcEnrollmentEnvelope)
                .filter(LarcEnrollmentEnvelope.fax_status == "in_progress",
                        LarcEnrollmentEnvelope.faxed_at.is_(None),
                        LarcEnrollmentEnvelope.last_synced_at <= cutoff,
                        LarcEnrollmentEnvelope.fax_terminally_failed_at.is_(None),
                        LarcEnrollmentEnvelope.status.notin_(
                            ("declined", "voided", "revoked", "expired", "faxed")))
                .all())
    reset = 0
    for env in wedged:
        claimed = db.query(LarcEnrollmentEnvelope).filter(
            LarcEnrollmentEnvelope.id == env.id,
            LarcEnrollmentEnvelope.fax_status == "in_progress",
        ).update(
            {LarcEnrollmentEnvelope.fax_status: "fax_failed",
             LarcEnrollmentEnvelope.last_fax_error:
                 (f"sweep: in_progress > {max_age_minutes}min — "
                  f"likely wedged by worker death; resetting"),
             LarcEnrollmentEnvelope.next_fax_retry_at: now},
            synchronize_session=False,
        )
        if claimed:
            reset += 1
    db.commit()
    return {"unwedged_fax_in_progress": reset}


def run_fax_retry_sweep() -> dict:
    """Entry point for the dedicated larc-fax-retry Cloud Run Job."""
    db = SessionLocal()
    try:
        # Unwedge any rows that died mid-fax before retrying the rest —
        # this brings them into the fax_failed bucket sweep_fax_retry
        # picks up.
        unwedge_result = sweep_unwedge_fax_in_progress(db)
        retry_result = sweep_fax_retry(db)
        return {**unwedge_result, **retry_result}
    finally:
        db.close()


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
