"""The pellet slot-materialization cron is wired into the scheduler."""
from app.services import fax_poller


def test_pellet_materialize_job_registered(monkeypatch):
    # Stub .start() so building the scheduler doesn't spin up background
    # threads or fire any job during the test.
    monkeypatch.setattr(fax_poller.BackgroundScheduler, "start", lambda self: None)
    sched = fax_poller.start_scheduler()
    jobs = {j.id: j for j in sched.get_jobs()}
    assert "pellet_slot_materialize" in jobs
    # Daily cron at 02:00.
    trigger = str(jobs["pellet_slot_materialize"].trigger)
    assert "hour='2'" in trigger and "minute='0'" in trigger


def test_materialize_job_callable():
    # The job function exists and is callable (it opens its own SessionLocal).
    assert callable(fax_poller._pellet_slot_materialize)
