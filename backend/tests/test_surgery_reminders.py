"""Surgery reminder cron + idempotency (I5)."""
from datetime import date, time, timedelta
from unittest.mock import patch

from app.models.surgery import Surgery, BlockDay, SurgerySlot
from app.models.patient_email import EmailTemplate, PatientEmail
from app.models.surgery_config import SurgeryConfig
from app.services.surgery_reminders import run_reminder_sweep


def _seed_template(db):
    db.add(EmailTemplate(
        kind="surgery_reminder", label="x",
        subject="Reminder: {{days_until}} days",
        html_body="<p>Hi {{patient_name}}, surgery on {{surgery_date}}</p>",
    ))


def _seed_surgery(db, days_out, email="pat@example.com"):
    s = Surgery(
        chart_number="1", patient_name="Pat", email=email,
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="confirmed",
        scheduled_date=date.today() + timedelta(days=days_out),
        procedures=[{"name": "Hyst", "kind": "robotic_180"}],
    )
    db.add(s); db.flush()
    return s


def test_sweep_sends_at_default_lead_days(db):
    _seed_template(db)
    s3 = _seed_surgery(db, 3)
    s1 = _seed_surgery(db, 1)
    _seed_surgery(db, 14)   # not at any lead day, should be skipped
    db.commit()

    with patch("app.services.patient_email.send_email", return_value=True):
        out = run_reminder_sweep(db)
    assert out["sent"] == 2
    assert out["skipped"] == 0
    kinds = (db.query(PatientEmail)
               .filter(PatientEmail.template_kind == "surgery_reminder")
               .count())
    assert kinds == 2


def test_sweep_is_idempotent(db):
    _seed_template(db)
    _seed_surgery(db, 3)
    db.commit()

    with patch("app.services.patient_email.send_email", return_value=True):
        run_reminder_sweep(db)
        out2 = run_reminder_sweep(db)
    assert out2["sent"] == 0
    assert out2["skipped"] == 1


def test_sweep_respects_custom_lead_days_from_config(db):
    _seed_template(db)
    _seed_surgery(db, 7)
    _seed_surgery(db, 1)
    db.add(SurgeryConfig(key="reminder_lead_days", value=[7]))
    db.commit()

    with patch("app.services.patient_email.send_email", return_value=True):
        out = run_reminder_sweep(db)
    # Only the 7-day-out surgery; the 1-day-out one is no longer a lead day.
    assert out["sent"] == 1


def test_sweep_skips_cancelled(db):
    _seed_template(db)
    s = _seed_surgery(db, 3)
    s.status = "cancelled"
    db.commit()

    with patch("app.services.patient_email.send_email", return_value=True):
        out = run_reminder_sweep(db)
    assert out["sent"] == 0
