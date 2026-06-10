"""CLI entrypoint for scheduled background jobs.

Run as a Cloud Run Job whose container args = [<job_name>]. Each
scheduler trigger in Cloud Scheduler points at the matching Cloud Run
Job. The wrapper functions called here are the same ones the legacy
APScheduler hooks into in app.services.fax_poller — so behavior is
identical to the in-process scheduler, just executed by Cloud Run.

Usage:
  python -m app.jobs.run <job_name>

Supported job names match the APScheduler `id`s in fax_poller.start_scheduler.
"""
import sys
import time
import logging

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jobs.run")

JOB_REGISTRY = {}


def register(name):
    def deco(fn):
        JOB_REGISTRY[name] = fn
        return fn
    return deco


# Each wrapper imports the underlying function lazily so a startup error
# in one service doesn't break unrelated jobs.

@register("fax_poller")
def fax_poller():
    from app.services.fax_poller import _tick
    _tick()


@register("checklist_generate")
def checklist_generate():
    from app.services.fax_poller import _checklist_daily_generate
    _checklist_daily_generate()


@register("checklist_morning")
def checklist_morning():
    from app.services.fax_poller import _checklist_morning_digest
    _checklist_morning_digest()


@register("checklist_eod")
def checklist_eod():
    from app.services.fax_poller import _checklist_eod_nudge
    _checklist_eod_nudge()


@register("checklist_escalations")
def checklist_escalations():
    from app.services.fax_poller import _checklist_escalation_sweep
    _checklist_escalation_sweep()


@register("google_workspace_sync")
def google_workspace_sync():
    from app.services.fax_poller import _google_workspace_sync
    _google_workspace_sync()


@register("surgery_escalations")
def surgery_escalations():
    from app.services.fax_poller import _surgery_escalation_sweep
    _surgery_escalation_sweep()


@register("surgery_release_sweep")
def surgery_release_sweep():
    from app.services.fax_poller import _surgery_release_sweep
    _surgery_release_sweep()


@register("larc_sweeps")
def larc_sweeps():
    from app.services.fax_poller import _larc_sweeps
    _larc_sweeps()


@register("larc_fax_retry")
def larc_fax_retry():
    """Drain the auto-fax retry queue: any envelope with fax_status=
    fax_failed and next_fax_retry_at <= now() is retried once. Marked
    terminally failed and emails info@ after the final allowed
    attempt. Scheduled by larc-fax-retry-trigger every 5 minutes during
    business hours."""
    from app.services.larc_sweeps import run_fax_retry_sweep
    run_fax_retry_sweep()


@register("surgery_auto_unresponsive")
def surgery_auto_unresponsive():
    """Mark surgeries as Unresponsive when the patient hasn't engaged
    for 30+ days past their pre-op visit. Scheduled by
    surgery-auto-unresponsive-trigger at 1:15 AM ET Mon-Fri."""
    from app.services.surgery_auto_unresponsive import run_auto_unresponsive_sweep
    run_auto_unresponsive_sweep()


@register("pellet_stale_sweep")
def pellet_stale_sweep():
    from app.services.fax_poller import _pellet_stale_sweep
    _pellet_stale_sweep()


@register("missing_charges_weekly")
def missing_charges_weekly():
    from app.services.fax_poller import _missing_charges_weekly_emails
    _missing_charges_weekly_emails()


@register("bank_recon_sweep")
def bank_recon_sweep():
    """Hourly sweep of bank-recon-csv/ — deletes consumed preview CSVs
    and any blob older than the hard TTL. Logic lives in
    app.services.bank_recon_sweep so the router endpoint can stay a
    thin wrapper. (Fable design review note 6.)"""
    from app.services.bank_recon_sweep import sweep_preview_csvs
    sweep_preview_csvs()


def main():
    if len(sys.argv) < 2:
        log.error("usage: python -m app.jobs.run <job_name>")
        log.error("known jobs: %s", ", ".join(sorted(JOB_REGISTRY)))
        sys.exit(2)
    name = sys.argv[1]
    fn = JOB_REGISTRY.get(name)
    if fn is None:
        log.error("unknown job: %s (known: %s)", name,
                  ", ".join(sorted(JOB_REGISTRY)))
        sys.exit(2)
    t0 = time.time()
    log.info("job %s starting", name)
    try:
        fn()
    except Exception:
        log.exception("job %s failed", name)
        raise
    elapsed = time.time() - t0
    log.info("job %s ok in %.2fs", name, elapsed)


if __name__ == "__main__":
    main()
