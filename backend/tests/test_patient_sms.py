"""Patient SMS foundation (J1)."""
from datetime import datetime
from unittest.mock import patch

from app.models.patient_sms import (
    SmsTemplate, PatientSms, SMS_TEMPLATE_KINDS, PATIENT_SMS_STATUSES,
)
from app.models.surgery import Surgery
from app.services.patient_sms import render, _segments, send_patient_sms


def _make_surgery(db, sms_consent=True, cell="+15555550100"):
    s = Surgery(
        chart_number="1", patient_name="Pat",
        cell_phone=cell,
        sms_consent=sms_consent,
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="confirmed",
    )
    db.add(s); db.commit(); db.refresh(s)
    return s


# ─── render() + segments ──────────────────────────────────────────

def test_render_substitutes():
    assert render("Hi {{name}}", {"name": "X"}) == "Hi X"


def test_segments_short_message_is_1():
    assert _segments("Hello") == 1


def test_segments_at_160_is_1():
    assert _segments("a" * 160) == 1


def test_segments_at_161_is_2():
    assert _segments("a" * 161) == 2


# ─── send_patient_sms() ──────────────────────────────────────────

def test_send_writes_audit_on_success(db):
    db.add(SmsTemplate(
        kind="sms_surgery_reminder", label="reminder",
        body="Hi {{name}}, surgery on {{date}}",
    ))
    s = _make_surgery(db)
    db.commit()

    with patch("app.services.patient_sms.send_sms", return_value=True):
        row = send_patient_sms(
            db, kind="sms_surgery_reminder",
            surgery=s,
            context={"name": "Pat", "date": "2026-06-15"},
            sent_by="ocooke@x.com",
        )

    assert row.status == "sent"
    assert row.rendered_body == "Hi Pat, surgery on 2026-06-15"
    assert row.to_phone == "+15555550100"
    assert row.segments == "1"


def test_send_skipped_when_no_consent(db):
    db.add(SmsTemplate(
        kind="sms_surgery_reminder", label="x", body="Hi {{name}}",
    ))
    s = _make_surgery(db, sms_consent=False)
    db.commit()

    with patch("app.services.patient_sms.send_sms") as mock_send:
        row = send_patient_sms(
            db, kind="sms_surgery_reminder",
            surgery=s, context={"name": "Pat"}, sent_by="x@y.com",
        )
        mock_send.assert_not_called()
    assert row.status == "skipped"
    assert "opted in" in row.failure_reason


def test_send_skipped_when_template_missing(db):
    s = _make_surgery(db)
    db.commit()

    with patch("app.services.patient_sms.send_sms") as mock_send:
        row = send_patient_sms(
            db, kind="sms_surgery_reminder",
            surgery=s, context={}, sent_by="x@y.com",
        )
        mock_send.assert_not_called()
    assert row.status == "skipped"
    assert "no active template" in row.failure_reason


def test_send_skipped_when_phone_blank(db):
    db.add(SmsTemplate(
        kind="sms_surgery_reminder", label="x", body="Hi",
    ))
    s = _make_surgery(db, cell=None)
    db.commit()

    row = send_patient_sms(
        db, kind="sms_surgery_reminder",
        surgery=s, context={}, sent_by="x@y.com",
    )
    assert row.status == "skipped"
    assert "blank" in row.failure_reason


def test_send_marked_failed_on_twilio_error(db):
    db.add(SmsTemplate(
        kind="sms_surgery_reminder", label="x", body="Hi",
    ))
    s = _make_surgery(db)
    db.commit()

    with patch("app.services.patient_sms.send_sms", return_value=False):
        row = send_patient_sms(
            db, kind="sms_surgery_reminder",
            surgery=s, context={}, sent_by="x@y.com",
        )
    assert row.status == "failed"


def test_ad_hoc_send(db):
    s = _make_surgery(db)
    db.commit()
    with patch("app.services.patient_sms.send_sms", return_value=True):
        row = send_patient_sms(
            db, kind=None,
            surgery=s,
            ad_hoc_body="Reminder: {{thing}} at {{time}}",
            context={"thing": "appt", "time": "9am"},
            sent_by="ocooke@x.com",
        )
    assert row.status == "sent"
    assert row.rendered_body == "Reminder: appt at 9am"
    assert row.template_kind is None


def test_template_kinds_includes_four():
    assert len(SMS_TEMPLATE_KINDS) == 4
    assert "sms_payment_link"          in SMS_TEMPLATE_KINDS
    assert "sms_surgery_confirmation"  in SMS_TEMPLATE_KINDS
    assert "sms_surgery_reminder"      in SMS_TEMPLATE_KINDS
    assert "sms_generic_message"       in SMS_TEMPLATE_KINDS
