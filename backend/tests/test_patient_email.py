"""Patient transactional email foundation (I1)."""
from unittest.mock import patch

from app.models.patient_email import (
    EmailTemplate, PatientEmail, EMAIL_TEMPLATE_KINDS, PATIENT_EMAIL_STATUSES,
)
from app.services.patient_email import render, send_patient_email


# ─── render() ──────────────────────────────────────────────────────

def test_render_substitutes_vars():
    out = render("Hi {{name}}, your appt is {{date}}.",
                 {"name": "Pat", "date": "2026-06-01"})
    assert out == "Hi Pat, your appt is 2026-06-01."


def test_render_missing_var_is_empty():
    assert render("Hello {{missing}}", {}) == "Hello "


def test_render_handles_whitespace_in_braces():
    out = render("Hi {{ name }}!", {"name": "X"})
    assert out == "Hi X!"


# ─── send_patient_email() — template path ─────────────────────────

def test_send_writes_audit_row_on_success(db):
    db.add(EmailTemplate(
        kind="surgery_confirmation", label="Surgery confirmation",
        subject="Your surgery is confirmed for {{date}}",
        html_body="<p>Hi {{name}}, see you on {{date}}.</p>",
    ))
    db.commit()

    with patch("app.services.patient_email.send_email", return_value=True):
        row = send_patient_email(
            db, kind="surgery_confirmation",
            to_email="pat@example.com",
            context={"name": "Pat", "date": "2026-06-15"},
            sent_by="ocooke@x.com",
            chart_number="1234",
        )

    assert row.status == "sent"
    assert row.rendered_subject == "Your surgery is confirmed for 2026-06-15"
    assert "Hi Pat, see you on 2026-06-15" in row.rendered_html
    assert row.to_email == "pat@example.com"
    assert row.template_kind == "surgery_confirmation"
    assert row.chart_number == "1234"


def test_send_marks_skipped_when_template_missing(db):
    with patch("app.services.patient_email.send_email") as mock_send:
        row = send_patient_email(
            db, kind="surgery_confirmation",  # no template exists
            to_email="pat@example.com",
            context={}, sent_by="x@y.com",
        )
        mock_send.assert_not_called()
    assert row.status == "skipped"
    assert "no active template" in row.failure_reason


def test_send_marks_skipped_when_template_inactive(db):
    db.add(EmailTemplate(
        kind="surgery_confirmation", label="x",
        subject="s", html_body="<p>x</p>", is_active=False,
    ))
    db.commit()

    row = send_patient_email(
        db, kind="surgery_confirmation",
        to_email="pat@example.com",
        context={}, sent_by="x@y.com",
    )
    assert row.status == "skipped"


def test_send_marks_skipped_when_to_email_blank(db):
    db.add(EmailTemplate(
        kind="surgery_confirmation", label="x", subject="s", html_body="<p>x</p>",
    ))
    db.commit()

    row = send_patient_email(
        db, kind="surgery_confirmation",
        to_email=None, context={}, sent_by="x@y.com",
    )
    assert row.status == "skipped"
    assert "blank" in row.failure_reason


def test_send_marks_failed_when_smtp_returns_false(db):
    db.add(EmailTemplate(
        kind="surgery_confirmation", label="x", subject="s", html_body="<p>x</p>",
    ))
    db.commit()

    with patch("app.services.patient_email.send_email", return_value=False):
        row = send_patient_email(
            db, kind="surgery_confirmation",
            to_email="pat@example.com",
            context={}, sent_by="x@y.com",
        )
    assert row.status == "failed"


# ─── send_patient_email() — ad-hoc path ───────────────────────────

def test_ad_hoc_send_renders_inline_subject_and_body(db):
    with patch("app.services.patient_email.send_email", return_value=True):
        row = send_patient_email(
            db, kind=None,
            to_email="pat@example.com",
            ad_hoc_subject="Hi {{name}}",
            ad_hoc_html="<p>From {{from}}</p>",
            context={"name": "Pat", "from": "Oliver"},
            sent_by="ocooke@x.com",
        )
    assert row.status == "sent"
    assert row.rendered_subject == "Hi Pat"
    assert row.template_kind is None


# ─── constants ─────────────────────────────────────────────────────

def test_template_kinds_includes_all_seven():
    assert "stripe_payment_link"      in EMAIL_TEMPLATE_KINDS
    assert "stripe_payment_receipt"   in EMAIL_TEMPLATE_KINDS
    assert "surgery_confirmation"     in EMAIL_TEMPLATE_KINDS
    assert "surgery_reminder"         in EMAIL_TEMPLATE_KINDS
    assert "docusign_consent_sent"    in EMAIL_TEMPLATE_KINDS
    assert "generic_patient_message"  in EMAIL_TEMPLATE_KINDS
    assert "surgery_post_op_followup" in EMAIL_TEMPLATE_KINDS


# ─── seed_default_email_templates() ───────────────────────────────

def test_seed_inserts_all_seven_templates(db):
    from app.services.surgery.config_seed import (
        seed_default_email_templates, DEFAULT_EMAIL_TEMPLATES,
    )

    n = seed_default_email_templates(db)
    assert n == len(DEFAULT_EMAIL_TEMPLATES) == 7

    # Re-run is a no-op
    n2 = seed_default_email_templates(db)
    assert n2 == 0

    # Every seeded template kind has a row. EMAIL_TEMPLATE_KINDS also
    # carries the legacy "docusign_consent_sent" kind (kept only so
    # historical patient_emails rows resolve; superseded by boldsign_*
    # and intentionally NOT seeded), so compare against the seed list.
    kinds_in_db = {t.kind for t in db.query(EmailTemplate).all()}
    assert {t["kind"] for t in DEFAULT_EMAIL_TEMPLATES} == kinds_in_db


def test_seed_does_not_overwrite_existing(db):
    from app.services.surgery.config_seed import seed_default_email_templates

    # Pre-existing admin-edited template
    db.add(EmailTemplate(
        kind="surgery_confirmation", label="custom",
        subject="Custom subject", html_body="<p>Custom body</p>",
    ))
    db.commit()

    seed_default_email_templates(db)
    row = (db.query(EmailTemplate)
             .filter(EmailTemplate.kind == "surgery_confirmation").first())
    assert row.label == "custom"
    assert row.subject == "Custom subject"


# ─── I7: ad-hoc endpoint tests ────────────────────────────────────

def test_send_ad_hoc_endpoint_writes_audit(client, db):
    from app.models.surgery import Surgery

    db.add(EmailTemplate(
        kind="generic_patient_message", label="x",
        subject="{{subject}}",
        html_body="<p>Hi {{patient_name}}</p>{{body}}<p>— {{sender_name}}</p>",
    ))
    s = Surgery(chart_number="1", patient_name="Pat",
                 email="pat@example.com",
                 eligible_facilities=["medstar"], selected_facility="medstar",
                 status="confirmed")
    db.add(s); db.commit()

    from unittest.mock import patch
    with patch("app.services.patient_email.send_email", return_value=True):
        resp = client.post(f"/api/surgery/{s.id}/send-patient-email", json={
            "subject": "Follow up question",
            "body_html": "<p>Quick check on your lab work.</p>",
        })
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"

    em = db.query(PatientEmail).filter(PatientEmail.surgery_id == s.id).first()
    assert em is not None
    assert em.rendered_subject == "Follow up question"
    assert "lab work" in em.rendered_html


def test_send_ad_hoc_rejects_blank_recipient(client, db):
    from app.models.surgery import Surgery
    db.add(EmailTemplate(
        kind="generic_patient_message", label="x",
        subject="{{subject}}", html_body="<p>{{body}}</p>",
    ))
    s = Surgery(chart_number="1", patient_name="Pat",
                 email=None,
                 eligible_facilities=["medstar"], selected_facility="medstar",
                 status="confirmed")
    db.add(s); db.commit()

    resp = client.post(f"/api/surgery/{s.id}/send-patient-email", json={
        "subject": "x", "body_html": "<p>y</p>",
    })
    assert resp.status_code == 422


# ─── I8: admin email-template endpoints ───────────────────────────

def test_list_email_templates_returns_seeded(client, db):
    from app.services.surgery.config_seed import seed_default_email_templates
    seed_default_email_templates(db)
    resp = client.get("/api/surgery/admin/email-templates")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["templates"]) == 7
    assert "stripe_payment_link" in body["allowed_kinds"]


def test_patch_email_template_persists(client, db):
    db.add(EmailTemplate(
        kind="surgery_confirmation", label="orig",
        subject="orig", html_body="<p>orig</p>",
    ))
    db.commit()
    t = db.query(EmailTemplate).filter_by(kind="surgery_confirmation").first()

    resp = client.patch(f"/api/surgery/admin/email-templates/{t.id}", json={
        "subject": "New subject",
        "is_active": False,
    })
    assert resp.status_code == 200
    db.refresh(t)
    assert t.subject == "New subject"
    assert t.is_active is False
    assert t.updated_by == "tester@waldorfwomenscare.com"


def test_preview_renders_with_context(client, db):
    resp = client.post("/api/surgery/admin/email-templates/preview", json={
        "subject":   "Hi {{name}}",
        "html_body": "<p>Pay <strong>${{amount}}</strong></p>",
        "context":   {"name": "Pat", "amount": "100.00"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["subject"]   == "Hi Pat"
    assert "100.00" in body["html_body"]
