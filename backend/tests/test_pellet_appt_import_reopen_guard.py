"""Regression: the ModMed appt-import cancel_missing sweep must not auto-cancel
a reopened visit. A reopened visit sits transiently in_progress while a manager
corrects it; if it's in the upload's date range but absent from the upload rows
(a past completed visit never reappears), the old sweep would silently cancel
it. The reopened_at guard prevents that."""
from datetime import date, timedelta

from app.models.pellet import PelletPatient, PelletVisit
from app.services.pellet.appt_import import import_appointments
from app.utils.dt import now_utc_naive


def _row(mrn, appt_date, status="Scheduled"):
    return {
        "mrn": mrn, "appt_date": appt_date, "appt_status": status,
        "first_name": "Up", "last_name": "Loaded", "dob": None,
        "phone": None, "email": None, "payer": None,
        "location": "White Plains", "provider": None,
        "patient_link": None, "appt_time": None,
    }


def test_cancel_missing_skips_reopened_visit(db):
    today = date.today()
    # A reopened visit (transiently in_progress), inside the upload date range,
    # NOT present in the uploaded rows.
    p = PelletPatient(patient_name="Reopened, Pat", chart_number="REOPEN-IMP")
    db.add(p); db.flush()
    v = PelletVisit(patient_id=p.id, status="in_progress",
                    scheduled_date=today - timedelta(days=2),
                    reopened_at=now_utc_naive(), reopened_by="mgr@x.com",
                    reopened_reason="fix lot", pre_reopen_status="billed")
    db.add(v); db.commit()
    vid = v.id

    # Upload rows for OTHER patients on dates that bracket the reopened visit,
    # so it falls inside [min_d, max_d] but its (chart, date) key isn't seen.
    rows = [
        _row("OTHER-1", today - timedelta(days=4)),
        _row("OTHER-2", today),
    ]
    report = import_appointments(db, rows, actor="tester", cancel_missing=True)

    assert report["visits_cancelled_missing"] == 0
    assert db.query(PelletVisit).get(vid).status == "in_progress"


def test_cancel_missing_still_cancels_plain_in_progress(db):
    """Control: a non-reopened in-range in_progress visit IS still swept."""
    today = date.today()
    p = PelletPatient(patient_name="Plain, Pat", chart_number="PLAIN-IMP")
    db.add(p); db.flush()
    v = PelletVisit(patient_id=p.id, status="in_progress",
                    scheduled_date=today - timedelta(days=2))
    db.add(v); db.commit()
    vid = v.id

    rows = [
        _row("OTHER-1", today - timedelta(days=4)),
        _row("OTHER-2", today),
    ]
    report = import_appointments(db, rows, actor="tester", cancel_missing=True)

    assert report["visits_cancelled_missing"] == 1
    assert db.query(PelletVisit).get(vid).status == "cancelled"
