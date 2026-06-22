from app.services.missing_charges_triage import (
    get_triage_recipients, set_triage_recipients, TRIAGE_RECIPIENTS_KEY,
)


def test_recipients_roundtrip(db):
    assert get_triage_recipients(db) == []
    set_triage_recipients(db, "a@wwc.com, b@wwc.com ,")
    assert get_triage_recipients(db) == ["a@wwc.com", "b@wwc.com"]


def test_recipients_endpoint(client, db):
    # super-admin `client` passes the MANAGE gate
    r = client.put("/api/billing/missing-charges/triage-recipients",
                   json={"recipients": ["x@wwc.com"]})
    assert r.status_code == 200
    g = client.get("/api/billing/missing-charges/triage-recipients")
    assert g.status_code == 200
    assert g.json()["recipients"] == ["x@wwc.com"]


from datetime import timedelta, date
from app.utils.dt import now_utc_naive
from app.models.missing_charge import MissingCharge
import app.services.missing_charges_triage as mct


def _new_row(db, mrn, days_ago=0):
    c = MissingCharge(patient_mrn=mrn, patient_name="Doe", appointment_date=date(2026, 1, 1),
                      primary_provider="Dr A", status="new")
    db.add(c); db.commit(); db.refresh(c)
    if days_ago:
        c.created_at = now_utc_naive() - timedelta(days=days_ago); db.commit()
    return c


def test_reminder_skips_when_no_untriaged(db):
    set_triage_recipients(db, "a@wwc.com")
    rep = mct.send_triage_reminders(db)
    assert rep["skipped"] == "no_untriaged"


def test_reminder_skips_when_no_recipients(db):
    _new_row(db, "M1")
    rep = mct.send_triage_reminders(db)
    assert rep["skipped"] == "no_recipients" and rep["count"] == 1


def test_reminder_sends_email_to_recipients(db, monkeypatch):
    _new_row(db, "M1", days_ago=4)
    _new_row(db, "M2")
    set_triage_recipients(db, "a@wwc.com")
    calls = []
    monkeypatch.setattr(mct, "send_email", lambda to, subj, html, text_body="": calls.append((to, subj)) or True)
    monkeypatch.setattr(mct, "send_slack_dm", lambda user, text: False)
    rep = mct.send_triage_reminders(db)
    assert rep["count"] == 2 and rep["oldest_days"] >= 4
    assert calls and calls[0][0] == "a@wwc.com"
