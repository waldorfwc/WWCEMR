"""Seed the LARC per-step notification templates (7 email + 7 SMS).

These back `notify_larc_step()` (app/services/larc/notifications.py): each
workflow milestone maps to a template `kind`, and the notifier skips the
send entirely if no template exists for that kind. Seeding here makes the
real patient-facing sends render.

Idempotent: insert only for `kind` values that don't have a row yet, so an
admin who edits a template's copy is never overwritten. Wired into
app.database.init_db().

Placeholders the notifier supplies (see notifications.py build context):
  {{patient_name}}, {{portal_url}}, {{amount}}, {{practice_phone}}
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.patient_email import EmailTemplate
from app.models.patient_sms import SmsTemplate


LARC_EMAIL_TEMPLATES = [
    {
        "kind": "larc_responsibility_due",
        "label": "LARC — payment needed",
        "subject": "Your device — payment needed",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>We've reviewed your insurance benefits for your device. Your remaining
patient responsibility is <strong>${{amount}}</strong>.</p>
<p>Please sign in to your secure portal to review the details and pay:</p>
<p><a href="{{portal_url}}">{{portal_url}}</a></p>
<p>Questions? Call us at {{practice_phone}}.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "larc_payment_receipt",
        "label": "LARC — payment received",
        "subject": "Payment received — thank you",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>We've received your payment of <strong>${{amount}}</strong> toward your
device. Thank you!</p>
<p>We'll be in touch with the next steps. Questions? Call {{practice_phone}}.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "larc_device_allocated",
        "label": "LARC — device reserved",
        "subject": "Your device is reserved",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>Good news — your device has been reserved for you. We'll let you know as
soon as it's ready for your insertion appointment.</p>
<p>Questions? Call us at {{practice_phone}}.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "larc_enrollment_ready",
        "label": "LARC — sign your enrollment form",
        "subject": "Sign your enrollment form",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>Your pharmacy enrollment form is ready for your signature. Please sign in
to your secure portal to complete it:</p>
<p><a href="{{portal_url}}">{{portal_url}}</a></p>
<p>Questions? Call us at {{practice_phone}}.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "larc_enrollment_faxed",
        "label": "LARC — enrollment submitted",
        "subject": "Your enrollment was submitted",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>Your signed enrollment form has been submitted to the pharmacy. We'll let
you know when your device arrives.</p>
<p>Questions? Call us at {{practice_phone}}.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "larc_device_received",
        "label": "LARC — device arrived",
        "subject": "Your device has arrived",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>Your device has arrived at our office. We'll reach out shortly to schedule
your insertion appointment.</p>
<p>Questions? Call us at {{practice_phone}}.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "larc_ready",
        "label": "LARC — device ready, call to schedule",
        "subject": "Your device is ready",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>Your device is ready! Please call us at <strong>{{practice_phone}}</strong>
to schedule your insertion appointment.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
]


LARC_SMS_TEMPLATES = [
    {
        "kind": "larc_responsibility_due",
        "label": "LARC SMS — payment needed",
        "body": "WWC Gyn: Your device responsibility is ${{amount}}. Pay securely: {{portal_url}} Reply STOP to opt out.",
    },
    {
        "kind": "larc_payment_receipt",
        "label": "LARC SMS — payment received",
        "body": "WWC Gyn: We received your ${{amount}} payment toward your device. Thank you! Reply STOP to opt out.",
    },
    {
        "kind": "larc_device_allocated",
        "label": "LARC SMS — device reserved",
        "body": "WWC Gyn: Your device has been reserved for you. We'll be in touch soon. Reply STOP to opt out.",
    },
    {
        "kind": "larc_enrollment_ready",
        "label": "LARC SMS — sign enrollment",
        "body": "WWC Gyn: Your enrollment form is ready to sign: {{portal_url}} Reply STOP to opt out.",
    },
    {
        "kind": "larc_enrollment_faxed",
        "label": "LARC SMS — enrollment submitted",
        "body": "WWC Gyn: Your enrollment was submitted to the pharmacy. We'll let you know when your device arrives. Reply STOP to opt out.",
    },
    {
        "kind": "larc_device_received",
        "label": "LARC SMS — device arrived",
        "body": "WWC Gyn: Your device has arrived at our office. We'll reach out to schedule. Reply STOP to opt out.",
    },
    {
        "kind": "larc_ready",
        "label": "LARC SMS — ready, call to schedule",
        "body": "WWC Gyn: Your device is ready! Call us at {{practice_phone}} to schedule. Reply STOP to opt out.",
    },
]


def seed_larc_templates(db: Session) -> int:
    """Insert any missing LARC notification templates. Idempotent — existing
    `kind` rows are left untouched. Returns the number of rows inserted."""
    inserted = 0
    for t in LARC_EMAIL_TEMPLATES:
        exists = (db.query(EmailTemplate)
                    .filter(EmailTemplate.kind == t["kind"])
                    .first())
        if exists:
            continue
        db.add(EmailTemplate(
            kind=t["kind"], label=t["label"],
            subject=t["subject"], html_body=t["html_body"],
            updated_by="seed",
        ))
        inserted += 1
    for t in LARC_SMS_TEMPLATES:
        exists = (db.query(SmsTemplate)
                    .filter(SmsTemplate.kind == t["kind"])
                    .first())
        if exists:
            continue
        db.add(SmsTemplate(
            kind=t["kind"], label=t["label"], body=t["body"],
            updated_by="seed",
        ))
        inserted += 1
    if inserted:
        db.commit()
    return inserted
