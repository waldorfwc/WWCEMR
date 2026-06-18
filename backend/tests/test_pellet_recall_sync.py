"""materialize_pellet_recalls: pellet-due patients become RecallEntry rows."""
from datetime import date, datetime, timedelta

from app.models.pellet import PelletPatient, PelletVisit
from app.models.recall import RecallEntry
from app.services.pellet.recall_sync import (materialize_pellet_recalls,
                                             PELLET_RECALL_TYPE)
from app.utils.dt import now_utc_naive


def _due_patient(db, chart):
    p = PelletPatient(chart_number=chart, patient_name=f"Pt {chart}", status="active",
                      patient_phone="3015551234", recall_interval_months=4)
    db.add(p); db.commit(); db.refresh(p)
    v = PelletVisit(patient_id=p.id, visit_kind="initial", status="billed",
                    inserted_at=now_utc_naive() - timedelta(days=200))
    db.add(v); db.commit()
    return p


def test_sync_creates_entry_for_due_patient(db):
    p = _due_patient(db, "DUE1")
    out = materialize_pellet_recalls(db)
    assert out["created"] == 1
    e = db.query(RecallEntry).filter(RecallEntry.recall_type == PELLET_RECALL_TYPE).one()
    assert e.chart_number == "DUE1" and e.status == "active"
    assert e.patient_name == "Pt DUE1" and e.cell_phone == "3015551234"
    assert e.recall_due is not None and e.last_visit is not None


def test_sync_is_idempotent_and_preserves_attempts(db):
    _due_patient(db, "DUE2")
    materialize_pellet_recalls(db)
    e = db.query(RecallEntry).filter(RecallEntry.recall_type == PELLET_RECALL_TYPE).one()
    e.attempts = 3; e.last_outcome = "Left voicemail"; db.commit()
    out = materialize_pellet_recalls(db)
    assert out["created"] == 0 and out["updated"] == 1
    db.refresh(e)
    assert e.attempts == 3 and e.last_outcome == "Left voicemail"


def test_sync_completes_entry_when_no_longer_due(db):
    p = _due_patient(db, "DUE3")
    materialize_pellet_recalls(db)
    db.add(PelletVisit(patient_id=p.id, visit_kind="repeat", status="new",
                       scheduled_date=date.today() + timedelta(days=10)))
    db.commit()
    out = materialize_pellet_recalls(db)
    assert out["completed"] == 1
    e = db.query(RecallEntry).filter(RecallEntry.recall_type == PELLET_RECALL_TYPE).one()
    assert e.status == "completed"


def test_sync_reactivates_completed_entry_when_due_again(db):
    p = _due_patient(db, "DUE4")
    materialize_pellet_recalls(db)
    e = db.query(RecallEntry).filter(RecallEntry.recall_type == PELLET_RECALL_TYPE).one()
    e.status = "completed"; e.attempts = 2; db.commit()
    out = materialize_pellet_recalls(db)
    assert out["created"] == 0 and out["updated"] == 1
    db.refresh(e)
    assert e.status == "active" and e.attempts == 2


def test_sync_leaves_suppressed_entry_alone(db):
    p = _due_patient(db, "DUE5")
    materialize_pellet_recalls(db)
    e = db.query(RecallEntry).filter(RecallEntry.recall_type == PELLET_RECALL_TYPE).one()
    e.status = "suppressed"; db.commit()
    out = materialize_pellet_recalls(db)
    # Still due, but suppressed: never updated, never completed.
    assert out["updated"] == 0 and out["completed"] == 0
    db.refresh(e)
    assert e.status == "suppressed"


def test_sync_ignores_wwe_entries(db):
    db.add(RecallEntry(chart_number="WWE1", recall_type="Est - Well-Woman Exam",
                       source="smartsheet", status="active"))
    db.commit()
    materialize_pellet_recalls(db)
    wwe = db.query(RecallEntry).filter(RecallEntry.recall_type == "Est - Well-Woman Exam").one()
    assert wwe.status == "active"


def test_cron_job_runs(db, monkeypatch):
    import app.services.fax_poller as fp
    # Allow the lock (3-arg: db, job_name, run_key) + use the test session.
    monkeypatch.setattr(fp, "claim_cron_run",
                        lambda *a, **k: True, raising=False)
    monkeypatch.setattr(fp, "SessionLocal", lambda: db, raising=False)
    fp._pellet_recall_sync()
