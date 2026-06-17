"""Cross-instance single-flight claim for scheduled jobs."""
from app.models.cron_run import CronRun
from app.services.cron_lock import claim_cron_run


def test_first_claim_wins_second_loses(db):
    assert claim_cron_run(db, "surgery_reminder", "2026-06-17") is True
    # A second instance claiming the same (job, key) loses → must skip.
    assert claim_cron_run(db, "surgery_reminder", "2026-06-17") is False
    assert db.query(CronRun).filter_by(job_name="surgery_reminder",
                                       run_key="2026-06-17").count() == 1


def test_new_run_key_claimable(db):
    assert claim_cron_run(db, "surgery_reminder", "2026-06-17") is True
    # Next day is a fresh claim.
    assert claim_cron_run(db, "surgery_reminder", "2026-06-18") is True


def test_distinct_jobs_independent(db):
    assert claim_cron_run(db, "checklist_morning_digest", "2026-06-17") is True
    assert claim_cron_run(db, "missing_charges_weekly", "2026-06-17") is True
    # Same day, different jobs → both win.
    assert db.query(CronRun).count() == 2
