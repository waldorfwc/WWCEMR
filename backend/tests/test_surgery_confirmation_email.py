"""Surgery confirmation email — fires from /select-slot, /schedule, /pick."""
from datetime import date, time, timedelta
from unittest.mock import patch

from app.models.surgery import Surgery, BlockDay, SurgerySlot
from app.models.patient_email import EmailTemplate, PatientEmail


def _seed_template(db):
    db.add(EmailTemplate(
        kind="surgery_confirmation", label="x",
        subject="Surgery on {{surgery_date}}",
        html_body="<p>Hi {{patient_name}}, see you at {{start_time}}</p>",
    ))
    db.commit()


def _seed_surgery_and_block(db, email="pat@example.com"):
    s = Surgery(
        chart_number="C001", patient_name="Pat", email=email,
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="in_progress",
        procedure_classification="robotic_180",
        procedures=[{"name": "Hyst", "kind": "robotic_180"}],
    )
    db.add(s); db.flush()
    bd = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=14),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(15, 0))
    db.add(bd); db.commit()
    return s, bd


def test_select_slot_sends_confirmation(client, db):
    _seed_template(db)
    s, bd = _seed_surgery_and_block(db)
    with patch("app.services.patient_email.send_email", return_value=True):
        resp = client.post(f"/api/p/surgery/{s.id}/select-slot", json={
            "block_day_id": str(bd.id), "start_time": "07:30",
        })
    assert resp.status_code == 200, resp.text
    em = (db.query(PatientEmail)
            .filter(PatientEmail.template_kind == "surgery_confirmation",
                     PatientEmail.surgery_id == s.id).first())
    assert em is not None
    assert em.to_email == "pat@example.com"
    assert em.status == "sent"
    assert "Pat" in em.rendered_html
    assert "07:30" in em.rendered_html


def test_coordinator_schedule_sends_confirmation(client, db):
    _seed_template(db)
    s, bd = _seed_surgery_and_block(db)
    with patch("app.services.patient_email.send_email", return_value=True):
        resp = client.post(f"/api/surgery/{s.id}/schedule", json={
            "block_day_id": str(bd.id), "start_time": "08:00",
        })
    assert resp.status_code == 200, resp.text
    em = (db.query(PatientEmail)
            .filter(PatientEmail.template_kind == "surgery_confirmation",
                     PatientEmail.surgery_id == s.id).first())
    assert em is not None


def test_no_email_when_patient_email_missing(client, db):
    _seed_template(db)
    s, bd = _seed_surgery_and_block(db, email=None)
    with patch("app.services.patient_email.send_email") as mock_send:
        resp = client.post(f"/api/surgery/{s.id}/schedule", json={
            "block_day_id": str(bd.id), "start_time": "08:00",
        })
        # send_email itself should never be called when to_email is blank
        assert mock_send.call_count == 0
    # PatientEmail row exists, marked skipped
    em = (db.query(PatientEmail)
            .filter(PatientEmail.template_kind == "surgery_confirmation",
                     PatientEmail.surgery_id == s.id).first())
    assert em is not None
    assert em.status == "skipped"
