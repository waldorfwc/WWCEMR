"""SMS sends fire alongside email for booking + reminder + composer (J3)."""
from datetime import date, time, timedelta
from unittest.mock import patch

from app.models.surgery import Surgery, BlockDay, SurgerySlot
from app.models.patient_sms import SmsTemplate, PatientSms
from app.models.patient_email import EmailTemplate


def _seed_templates(db):
    db.add(SmsTemplate(
        kind="sms_surgery_confirmation", label="x",
        body="WWC: surgery on {{surgery_date}} at {{start_time}}",
    ))
    db.add(SmsTemplate(
        kind="sms_surgery_reminder", label="x",
        body="WWC: reminder — surgery in {{days_until}} days",
    ))
    db.add(SmsTemplate(
        kind="sms_generic_message", label="x",
        body="WWC: {{body}}",
    ))
    db.add(EmailTemplate(
        kind="surgery_confirmation", label="x",
        subject="x", html_body="<p>x</p>",
    ))
    db.commit()


def _seed_surgery_consented(db, **kw):
    defaults = dict(
        chart_number="1", patient_name="Pat",
        email="pat@example.com", cell_phone="+15555550100",
        sms_consent=True,
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="in_progress",
        procedures=[{"name": "Hyst", "kind": "robotic_180"}],
    )
    defaults.update(kw)
    s = Surgery(**defaults)
    db.add(s); db.flush()
    return s


def test_select_slot_sends_confirmation_sms(client, db):
    _seed_templates(db)
    s = _seed_surgery_consented(db)
    bd = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=14),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(15, 0))
    db.add(bd); db.commit()

    with patch("app.services.patient_sms.send_sms", return_value=True), \
         patch("app.services.patient_email.send_email", return_value=True):
        resp = client.post(f"/api/p/surgery/{s.id}/select-slot", json={
            "block_day_id": str(bd.id), "start_time": "07:30",
        })
    assert resp.status_code == 200

    rows = (db.query(PatientSms)
              .filter(PatientSms.template_kind == "sms_surgery_confirmation",
                       PatientSms.surgery_id == s.id).all())
    assert len(rows) == 1
    assert rows[0].status == "sent"


def test_select_slot_skips_sms_when_no_consent(client, db):
    _seed_templates(db)
    s = _seed_surgery_consented(db); s.sms_consent = False; db.commit()
    bd = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=14),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(15, 0))
    db.add(bd); db.commit()

    with patch("app.services.patient_sms.send_sms") as mock_send, \
         patch("app.services.patient_email.send_email", return_value=True):
        client.post(f"/api/p/surgery/{s.id}/select-slot", json={
            "block_day_id": str(bd.id), "start_time": "07:30",
        })
        # Twilio should NOT be called at all
        assert mock_send.call_count == 0
    # PatientSms row still recorded with status=skipped
    row = (db.query(PatientSms)
             .filter(PatientSms.surgery_id == s.id).first())
    assert row.status == "skipped"


def test_reminder_cron_sends_both_email_and_sms(db):
    _seed_templates(db)
    from app.services.surgery_reminders import run_reminder_sweep
    s = _seed_surgery_consented(db,
        scheduled_date=date.today() + timedelta(days=3), status="confirmed")
    db.commit()

    with patch("app.services.patient_sms.send_sms", return_value=True), \
         patch("app.services.patient_email.send_email", return_value=True):
        out = run_reminder_sweep(db)
    assert out["sent"] >= 1   # email send counted
    sms_rows = (db.query(PatientSms)
                  .filter(PatientSms.template_kind == "sms_surgery_reminder",
                           PatientSms.surgery_id == s.id).all())
    assert len(sms_rows) == 1


def test_reminder_sms_is_idempotent(db):
    _seed_templates(db)
    from app.services.surgery_reminders import run_reminder_sweep
    _seed_surgery_consented(db,
        scheduled_date=date.today() + timedelta(days=3), status="confirmed")
    db.commit()

    with patch("app.services.patient_sms.send_sms", return_value=True), \
         patch("app.services.patient_email.send_email", return_value=True):
        run_reminder_sweep(db)
        # Re-run — no second SMS row
        run_reminder_sweep(db)
    sms_rows = (db.query(PatientSms)
                  .filter(PatientSms.template_kind == "sms_surgery_reminder").all())
    assert len(sms_rows) == 1


def test_ad_hoc_sms_endpoint(client, db):
    _seed_templates(db)
    s = _seed_surgery_consented(db)
    db.commit()

    with patch("app.services.patient_sms.send_sms", return_value=True):
        resp = client.post(f"/api/surgery/{s.id}/send-patient-sms", json={
            "body": "Quick check on your labs",
        })
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"

    row = db.query(PatientSms).filter(PatientSms.surgery_id == s.id).first()
    assert row is not None
    assert "Quick check on your labs" in row.rendered_body
