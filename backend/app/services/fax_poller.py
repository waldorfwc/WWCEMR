"""Poll RingCentral for outstanding fax statuses and update FaxLog rows."""
import os
from datetime import datetime, timedelta
from app.utils.dt import now_utc_naive
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler

from app.database import SessionLocal
from app.models.fax_log import FaxLog, FaxLogStatus
from app.services.fax_service import check_fax_status
from app.services.audit_service import ACTOR_SYSTEM, log_action

POLL_INTERVAL_MINUTES = int(os.environ.get("FAX_POLL_INTERVAL_MINUTES", "2"))
# Keep re-polling for 24 hours — RingCentral occasionally takes hours to
# return a Sent/Delivered confirmation for outbound faxes, and a 60-minute
# cutoff left rows frozen as "sent" forever (UI label: "⟳ Sending").
POLL_MAX_AGE_MINUTES = int(os.environ.get("FAX_POLL_MAX_AGE_MINUTES", "1440"))


# RingCentral statuses → our FaxLogStatus
_DELIVERED_STATES = {"Sent", "Delivered", "Received"}
_FAILED_STATES = {"SendingFailed", "DeliveryFailed", "Failed"}
_IN_FLIGHT_STATES = {"Queued", "Sending"}


def poll_outstanding_faxes(db: Session) -> int:
    """One polling pass. Returns the number of rows whose status transitioned."""
    cutoff = now_utc_naive() - timedelta(minutes=POLL_MAX_AGE_MINUTES)
    candidates = (
        db.query(FaxLog)
        .filter(
            FaxLog.status.in_([FaxLogStatus.QUEUED, FaxLogStatus.SENT]),
            FaxLog.sent_at >= cutoff,
            FaxLog.ringcentral_message_id.isnot(None),
        )
        .all()
    )

    changed = 0
    now = now_utc_naive()
    for row in candidates:
        try:
            rc = check_fax_status(row.ringcentral_message_id)
        except Exception as e:
            # Don't fail the batch; mark last_checked_at and continue
            row.last_checked_at = now
            db.commit()
            continue

        rc_status = (rc.get("status") or "").strip() if rc else ""
        row.last_checked_at = now

        if rc_status in _DELIVERED_STATES:
            if row.status != FaxLogStatus.DELIVERED:
                row.status = FaxLogStatus.DELIVERED
                row.delivered_at = now
                changed += 1
                log_action(db, "FAX_DELIVERED", "fax", actor=ACTOR_SYSTEM,
                           resource_id=str(row.id),
                           description=f"Fax {row.ringcentral_message_id} delivered")
        elif rc_status in _FAILED_STATES:
            if row.status != FaxLogStatus.FAILED:
                row.status = FaxLogStatus.FAILED
                row.error = rc.get("error") or rc_status
                changed += 1
                log_action(db, "FAX_FAILED", "fax", actor=ACTOR_SYSTEM,
                           resource_id=str(row.id),
                           description=f"Fax {row.ringcentral_message_id} failed: {row.error}")
        # In-flight / unknown → leave status alone

        db.commit()

    return changed


def _tick():
    db = SessionLocal()
    try:
        poll_outstanding_faxes(db)
    finally:
        db.close()


def _is_weekend_today() -> bool:
    """No checklist notifications on Sat/Sun (per practice policy).
    Generation still runs so a Friday-rollover for a 1st-of-month task
    that hits Saturday gets the right Monday instance."""
    from datetime import date
    return date.today().weekday() >= 5


def _checklist_daily_generate():
    """At midnight: spawn today's task-instances for every active template
    × user. Idempotent — safe to run multiple times."""
    from datetime import date
    from app.services.checklist_service import generate_instances_for_date
    db = SessionLocal()
    try:
        generate_instances_for_date(db, date.today())
    finally:
        db.close()


def _checklist_morning_digest():
    """At 7:30 AM: email + per-user Slack DM with today's task list.
    Also posts a one-line summary to the team channel.

    Skipped on Sat/Sun — no notifications on weekends.
    """
    if _is_weekend_today():
        return
    from datetime import date
    from app.models.user import User
    from app.services.checklist_service import my_today
    from app.services.checklist_notifications import send_morning_digest, send_team_summary
    db = SessionLocal()
    try:
        from app.services.cron_lock import claim_cron_run
        if not claim_cron_run(db, "checklist_morning_digest", date.today().isoformat()):
            return
        users = db.query(User).filter(User.groups.any()).all()
        sent_users = 0
        sent_tasks = 0
        for u in users:
            tasks = my_today(db, u.email, date.today())
            pending = [t for t in tasks if t["status"] in ("pending", "in_progress")]
            if pending:
                send_morning_digest(u, pending, db=db)
                sent_users += 1
                sent_tasks += len(pending)
        send_team_summary(sent_users, sent_tasks)
    finally:
        db.close()


def _checklist_eod_nudge():
    """At 5 PM: per-user DM + email nudge for pending tasks.
    Skipped on Sat/Sun — no notifications on weekends."""
    if _is_weekend_today():
        return
    from datetime import date
    from app.models.user import User
    from app.services.checklist_service import my_today
    from app.services.checklist_notifications import send_eod_overdue_nudge
    db = SessionLocal()
    try:
        from app.services.cron_lock import claim_cron_run
        if not claim_cron_run(db, "checklist_eod_nudge", date.today().isoformat()):
            return
        users = db.query(User).filter(User.groups.any()).all()
        for u in users:
            tasks = my_today(db, u.email, date.today())
            pending = [t for t in tasks if t["status"] in ("pending", "in_progress")]
            if pending:
                send_eod_overdue_nudge(u, pending, db=db)
    finally:
        db.close()


def _pellet_recall_sync():
    """Daily: refresh the pellet recall worklist (idempotent)."""
    from datetime import date
    db = SessionLocal()
    try:
        from app.services.cron_lock import claim_cron_run
        if not claim_cron_run(db, "pellet_recall_sync", date.today().isoformat()):
            return
        from app.services.pellet.recall_sync import materialize_pellet_recalls
        materialize_pellet_recalls(db)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Pellet recall sync error: %s", exc)
    finally:
        db.close()


def _pellet_stale_sweep():
    """Nightly: auto-cancel pellet visits 7+ days past scheduled with
    still-proposed dose lines. Returns stock for any pulled-but-not-inserted
    doses. Required so daily counts aren't permanently blocked."""
    try:
        from app.services.pellet.stale_sweep import run_sweep_job
        run_sweep_job()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Pellet stale sweep error: %s", exc)


def _pellet_slot_materialize():
    """Nightly: roll the pellet scheduling horizon forward — materialize open
    slots from active availability templates for the configured horizon.
    Idempotent (unique location+date+start_time), so re-running is safe."""
    db = SessionLocal()
    try:
        from app.services.pellet.scheduling import materialize_pellet_slots
        rep = materialize_pellet_slots(db)
        db.commit()
        import logging
        logging.getLogger(__name__).info(
            "Pellet slot materialization: %d created (horizon %dd)",
            rep.get("created", 0), rep.get("horizon_days", 0))
    except Exception as exc:
        db.rollback()
        import logging
        logging.getLogger(__name__).warning("Pellet slot materialization error: %s", exc)
    finally:
        db.close()


def _larc_sweeps():
    """Daily: expiry hold, stale-assignment reallocation, pharmacy SLA logging."""
    if _is_weekend_today():
        return
    try:
        from app.services.larc.sweeps import run_all
        run_all()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("LARC sweeps error: %s", exc)


def _surgery_release_sweep():
    """Daily: alert scheduler about unbooked hospital days within 14 days
    and under-booked office procedure days 6 days out. Skipped on weekends."""
    if _is_weekend_today():
        return
    from app.services.surgery.release_alerts import run_release_sweep
    db = SessionLocal()
    try:
        run_release_sweep(db)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Surgery release sweep error: %s", exc)
    finally:
        db.close()


def _surgery_escalation_sweep():
    """Hourly: notify managers of surgery milestones >48h behind schedule.
    Skipped on Sat/Sun — accumulates over the weekend, fires Mon."""
    if _is_weekend_today():
        return
    from app.services.surgery.escalations import run_escalation_sweep
    db = SessionLocal()
    try:
        run_escalation_sweep(db)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Surgery escalation error: %s", exc)
    finally:
        db.close()


def _boarding_slip_autosend():
    """Hourly: email the boarding slip to per-facility recipients once a
    surgery date has been selected for the configured number of hours.
    Guarded so only one instance runs per hour across instances."""
    db = SessionLocal()
    try:
        from app.services.cron_lock import claim_cron_run
        from app.services.surgery.boarding_slip_email import auto_email_sweep
        # one run per hour across instances
        run_key = now_utc_naive().strftime("%Y-%m-%dT%H")
        if not claim_cron_run(db, "surgery_boarding_slip_autosend", run_key):
            return
        auto_email_sweep(db)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Boarding-slip autosend error: %s", exc)
    finally:
        db.close()


def _google_workspace_sync():
    """Hourly: pull every user from Google Workspace and reconcile is_active.
    No-ops silently when GOOGLE_WORKSPACE_* env vars aren't configured."""
    from app.services import google_sync
    if not google_sync.is_configured():
        return
    db = SessionLocal()
    try:
        google_sync.run_sync(db, triggered_by="system:cron")
    except Exception as exc:
        # Service already logs; swallow so the scheduler keeps running.
        import logging
        logging.getLogger(__name__).warning("Google sync error: %s", exc)
    finally:
        db.close()


def _checklist_escalation_sweep():
    """Hourly: notify managers of any of their templates' tasks that are
    past escalate_after_hours without an answer. Idempotent — instances
    are stamped with escalation_sent_at on first notify so each manager
    only hears about a given task once.

    Skipped on Sat/Sun — accumulates over the weekend and fires on Mon.
    """
    if _is_weekend_today():
        return
    from app.services.checklist_notifications import run_escalation_sweep
    db = SessionLocal()
    try:
        run_escalation_sweep(db)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(_tick, "interval", minutes=POLL_INTERVAL_MINUTES, id="fax_poller",
                  max_instances=1, coalesce=True)
    # Checklist jobs
    sched.add_job(_checklist_daily_generate, "cron", hour=0, minute=5,
                  id="checklist_generate", max_instances=1, coalesce=True)
    sched.add_job(_checklist_morning_digest, "cron", hour=7, minute=30,
                  id="checklist_morning", max_instances=1, coalesce=True)
    sched.add_job(_checklist_eod_nudge, "cron", hour=17, minute=0,
                  id="checklist_eod", max_instances=1, coalesce=True)
    # Manager escalations — every hour during the work day so a missed
    # task triggers within an hour of crossing the escalate window.
    sched.add_job(_checklist_escalation_sweep, "cron",
                  day_of_week="mon-fri", hour="8-18", minute=15,
                  id="checklist_escalations", max_instances=1, coalesce=True)
    # Google Workspace sync — hourly. Skips itself when env not configured.
    sched.add_job(_google_workspace_sync, "cron", minute=30,
                  id="google_workspace_sync", max_instances=1, coalesce=True)
    # Surgery escalations — every hour Mon-Fri 8 AM-6 PM.
    sched.add_job(_surgery_escalation_sweep, "cron",
                  day_of_week="mon-fri", hour="8-18", minute=45,
                  id="surgery_escalations", max_instances=1, coalesce=True)
    # Boarding-slip auto-send — hourly; the sweep self-gates on the
    # configured enable flag + per-surgery elapsed-hours threshold.
    sched.add_job(_boarding_slip_autosend, "cron", minute=15,
                  id="surgery_boarding_slip_autosend", max_instances=1, coalesce=True)
    # Surgery release alerts — once daily Mon-Fri at 9 AM.
    sched.add_job(_surgery_release_sweep, "cron",
                  day_of_week="mon-fri", hour=9, minute=0,
                  id="surgery_release_sweep", max_instances=1, coalesce=True)
    # LARC daily sweeps (expiry hold + stale reallocate + pharmacy SLA)
    sched.add_job(_larc_sweeps, "cron",
                  day_of_week="mon-fri", hour=9, minute=15,
                  id="larc_sweeps", max_instances=1, coalesce=True)
    # Pellet stale-visit auto-cancel — nightly at 00:30
    sched.add_job(_pellet_stale_sweep, "cron", hour=0, minute=30,
                  id="pellet_stale_sweep", max_instances=1, coalesce=True)
    # Pellet scheduling — roll the slot horizon forward nightly at 2 AM.
    sched.add_job(_pellet_slot_materialize, "cron", hour=2, minute=0,
                  id="pellet_slot_materialize", max_instances=1, coalesce=True)
    # Pellet recall sync — refresh the recall worklist daily at 3 AM.
    sched.add_job(_pellet_recall_sync, "cron", hour=3, minute=0,
                  id="pellet_recall_sync", replace_existing=True,
                  max_instances=1, coalesce=True)
    # Missing-charges provider emails — Monday 8 AM weekly
    sched.add_job(_missing_charges_weekly_emails, "cron",
                  day_of_week="mon", hour=8, minute=0,
                  id="missing_charges_weekly", max_instances=1, coalesce=True)
    # Phase I — daily patient surgery reminders at 8 AM.
    sched.add_job(_reminder_job, "cron", hour=8, minute=0,
                  id="surgery_reminder_sweep", replace_existing=True,
                  max_instances=1, coalesce=True)
    sched.start()
    return sched


def _missing_charges_weekly_emails():
    from datetime import date
    db = SessionLocal()
    try:
        from app.services.cron_lock import claim_cron_run
        if not claim_cron_run(db, "missing_charges_weekly", date.today().isoformat()):
            return
        from app.services.missing_charges_email import send_provider_emails
        report = send_provider_emails(db, triggered_by="system:weekly-cron")
        log.info("Missing-charges weekly email run: %d providers, %d sent, %d skipped",
                 len(report["providers"]), report["sent_count"], report["skipped_count"])
    finally:
        db.close()


def _reminder_job():
    """Phase I — daily patient surgery reminders at 8 AM."""
    from datetime import date
    db = SessionLocal()
    try:
        from app.services.cron_lock import claim_cron_run
        if not claim_cron_run(db, "surgery_reminder", date.today().isoformat()):
            return
        from app.services.surgery.reminders import run_reminder_sweep
        run_reminder_sweep(db)
    finally:
        db.close()
