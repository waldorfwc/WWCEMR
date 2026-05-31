"""DocuSign consent-sent email (I6).

Verifies that hitting POST /api/surgery/<id>/consent/docusign-send writes a
PatientEmail row with kind='docusign_consent_sent' after a successful envelope
creation. The DocuSign HTTP call and the SMTP send are both mocked out.
"""
from datetime import date, timedelta
from unittest.mock import patch

from app.models.surgery import Surgery, ConsentTemplate
from app.models.patient_email import EmailTemplate, PatientEmail


# ── helpers ────────────────────────────────────────────────────────────────

def _seed_email_template(db):
    db.add(EmailTemplate(
        kind="docusign_consent_sent",
        label="DocuSign consent sent",
        subject="Sign your consent forms",
        html_body="<p>Hi {{patient_name}}, your surgery is on {{surgery_date}}.</p>",
    ))
    db.commit()


def _seed_surgery(db, email="pat@example.com"):
    s = Surgery(
        chart_number="C999",
        patient_name="Jane Doe",
        email=email,
        status="in_progress",
        procedures=[{"name": "Hysterectomy", "kind": "robotic_180"}],
        scheduled_date=date.today() + timedelta(days=30),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _seed_consent_template(db, procedure_keyword="hysterectomy"):
    ct = ConsentTemplate(
        name="Robotic Hysterectomy Consent",
        docusign_template_id="FAKE-DS-TMPL-001",
        procedure_match=[procedure_keyword],
    )
    db.add(ct)
    db.commit()
    db.refresh(ct)
    return ct


# ── tests ──────────────────────────────────────────────────────────────────

def test_docusign_send_writes_consent_email(client, db):
    """Happy path: one envelope sent → PatientEmail row written."""
    _seed_email_template(db)
    s = _seed_surgery(db)
    _seed_consent_template(db)

    fake_envelope_id = "aabbccdd-0000-0000-0000-000000000001"

    with (
        patch(
            "app.services.docusign_envelopes._create_envelope",
            return_value=fake_envelope_id,
        ),
        patch("app.services.patient_email.send_email", return_value=True),
    ):
        resp = client.post(f"/api/surgery/{s.id}/consent/docusign-send")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["sent"]) == 1
    assert data["sent"][0]["envelope_id"] == fake_envelope_id

    em = (
        db.query(PatientEmail)
        .filter(
            PatientEmail.template_kind == "docusign_consent_sent",
            PatientEmail.surgery_id == s.id,
        )
        .first()
    )
    assert em is not None, "PatientEmail row not written"
    assert em.to_email == "pat@example.com"
    assert em.status == "sent"
    assert "Jane Doe" in em.rendered_html
    assert str(date.today() + timedelta(days=30)) in em.rendered_html


def test_docusign_send_no_email_when_all_skipped(client, db):
    """If every template is already-sent (skipped), no new email is fired."""
    _seed_email_template(db)
    s = _seed_surgery(db)
    ct = _seed_consent_template(db)

    # Pre-populate an envelope row so the service skips this template
    from app.models.surgery import SurgeryConsentEnvelope
    db.add(SurgeryConsentEnvelope(
        surgery_id=s.id,
        template_id=ct.id,
        docusign_envelope_id="existing-envelope-xyz",
        status="sent",
    ))
    db.commit()

    with (
        patch("app.services.docusign_envelopes._create_envelope") as mock_create,
        patch("app.services.patient_email.send_email") as mock_smtp,
    ):
        resp = client.post(f"/api/surgery/{s.id}/consent/docusign-send")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sent"] == []
    assert len(data["skipped"]) == 1

    # _create_envelope never called, email never sent
    mock_create.assert_not_called()
    mock_smtp.assert_not_called()

    # No new PatientEmail row
    count = (
        db.query(PatientEmail)
        .filter(PatientEmail.template_kind == "docusign_consent_sent")
        .count()
    )
    assert count == 0


def test_docusign_send_email_skipped_when_no_patient_email(client, db):
    """No patient email address → PatientEmail row written with status='skipped'."""
    _seed_email_template(db)
    s = _seed_surgery(db, email=None)
    _seed_consent_template(db)

    fake_envelope_id = "aabbccdd-0000-0000-0000-000000000002"

    with (
        patch(
            "app.services.docusign_envelopes._create_envelope",
            return_value=fake_envelope_id,
        ),
        patch("app.services.patient_email.send_email") as mock_smtp,
    ):
        resp = client.post(f"/api/surgery/{s.id}/consent/docusign-send")

    assert resp.status_code == 200, resp.text

    # send_email should never fire (no to_email)
    mock_smtp.assert_not_called()

    em = (
        db.query(PatientEmail)
        .filter(
            PatientEmail.template_kind == "docusign_consent_sent",
            PatientEmail.surgery_id == s.id,
        )
        .first()
    )
    assert em is not None
    assert em.status == "skipped"
