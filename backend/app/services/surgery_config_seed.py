"""Seed default surgery-config rows on init.

Idempotent: re-running is a no-op once rows exist. Wired into
app.database.init_db().
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.surgery_config import Facility, SurgeryProcedureTemplate
from app.models.patient_email import EmailTemplate
from app.models.patient_sms import SmsTemplate


DEFAULT_FACILITIES = [
    {"code": "office",  "label": "WWC Office — White Plains",
     "address": "White Plains, MD", "sort_order": 1},
    {"code": "medstar", "label": "MedStar Southern Maryland Hospital",
     "address": "7503 Surratts Rd, Clinton, MD", "sort_order": 2},
    {"code": "crmc",    "label": "University of MD Charles Regional",
     "address": "5 Garrett Ave, La Plata, MD", "sort_order": 3},
]


DEFAULT_TEMPLATES = [
    {"code": "office_30",   "name": "Office procedure (30 min)",
     "procedure_kind": "office",       "default_duration_minutes": 30},
    {"code": "minor_60",    "name": "Minor procedure (60 min)",
     "procedure_kind": "minor",        "default_duration_minutes": 60},
    {"code": "major_120",   "name": "Major procedure (120 min)",
     "procedure_kind": "major",        "default_duration_minutes": 120},
    {"code": "robotic_180", "name": "Robotic surgery (180 min)",
     "procedure_kind": "robotic_180",  "default_duration_minutes": 180,
     "default_cpt_code": "58571"},
    {"code": "robotic_240", "name": "Robotic surgery (240 min)",
     "procedure_kind": "robotic_240",  "default_duration_minutes": 240,
     "default_cpt_code": "58572"},
]


def seed_default_templates(db: Session) -> int:
    inserted = 0
    for t in DEFAULT_TEMPLATES:
        if db.query(SurgeryProcedureTemplate).filter(
                SurgeryProcedureTemplate.code == t["code"]).first():
            continue
        db.add(SurgeryProcedureTemplate(**t, created_by="seed", updated_by="seed"))
        inserted += 1
    if inserted:
        db.commit()
    return inserted


DEFAULT_EMAIL_TEMPLATES = [
    {
        "kind": "stripe_payment_link",
        "label": "Stripe — payment link to patient",
        "subject": "Payment requested for your upcoming surgery — WWC Gynecology & Aesthetics",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>Your insurance pre-op balance is <strong>${{amount}}</strong>. You can pay securely
using the link below:</p>
<p><a href=\"{{checkout_url}}\" style=\"display:inline-block;padding:10px 20px;background:#7c3aed;color:#fff;text-decoration:none;border-radius:4px;\">Pay ${{amount}}</a></p>
<p>If you have questions, reply to this email or call our scheduler.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "stripe_payment_receipt",
        "label": "Stripe — payment receipt to patient",
        "subject": "Payment received — thank you",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>We've received your payment of <strong>${{amount}}</strong>. Thank you!</p>
<p>This payment is recorded against your upcoming surgery on {{surgery_date}}.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "surgery_confirmation",
        "label": "Surgery — date confirmation to patient",
        "subject": "Your surgery is confirmed for {{surgery_date}}",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>Your surgery has been scheduled:</p>
<ul>
  <li><strong>Date:</strong> {{surgery_date}}</li>
  <li><strong>Time:</strong> {{start_time}}</li>
  <li><strong>Location:</strong> {{facility}}</li>
  <li><strong>Procedure:</strong> {{procedure}}</li>
</ul>
<p>We'll send you reminders and any pre-op paperwork as the date approaches.
If anything changes, reply to this email or call our scheduler.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "surgery_reminder",
        "label": "Surgery — reminder to patient",
        "subject": "Reminder: surgery in {{days_until}} days",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>This is a friendly reminder that your surgery is in <strong>{{days_until}} days</strong>:</p>
<ul>
  <li><strong>Date:</strong> {{surgery_date}}</li>
  <li><strong>Time:</strong> {{start_time}}</li>
  <li><strong>Location:</strong> {{facility}}</li>
</ul>
<p>If you have outstanding consent forms, labs, or payments, please complete
those as soon as possible. Reply to this email or call us with any questions.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "docusign_consent_sent",
        "label": "DocuSign — consent forms ready to sign",
        "subject": "Please sign your surgery consent forms",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>Your surgery consent forms are ready for your signature. You'll receive
a separate email from DocuSign with the signing link.</p>
<p>Please sign at your earliest convenience — we need the signed forms on
file before your surgery on {{surgery_date}}.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "generic_patient_message",
        "label": "Generic — staff-composed message",
        "subject": "{{subject}}",
        "html_body": """\
<p>Hi {{patient_name}},</p>
{{body}}
<p>&mdash; {{sender_name}}<br>WWC Gynecology &amp; Aesthetics</p>
""",
    },
    {
        "kind": "surgery_post_op_followup",
        "label": "Surgery — post-op follow-up",
        "subject": "How are you feeling after your surgery?",
        "html_body": """\
<p>Hi {{patient_name}},</p>
<p>It's been a few days since your surgery on {{surgery_date}}. We hope your
recovery is going well.</p>
<p>If you're experiencing fever, increased pain, redness, or unusual bleeding,
please call our office immediately. For routine post-op questions, you can
reply to this email.</p>
<p>&mdash; WWC Gynecology &amp; Aesthetics</p>
""",
    },
]


def seed_default_email_templates(db: Session) -> int:
    """Idempotent: insert templates only for `kind` values that don't have
    a row yet. Existing templates (e.g. ones an admin edited) are never
    overwritten."""
    inserted = 0
    for t in DEFAULT_EMAIL_TEMPLATES:
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
    if inserted:
        db.commit()
    return inserted


DEFAULT_SMS_TEMPLATES = [
    {
        "kind": "sms_payment_link",
        "label": "SMS — payment link",
        "body": "WWC Gyn: Your pre-op balance of ${{amount}} is ready to pay: {{checkout_url}} Reply STOP to opt out.",
    },
    {
        "kind": "sms_surgery_confirmation",
        "label": "SMS — surgery confirmation",
        "body": "WWC Gyn: Your surgery is confirmed for {{surgery_date}} at {{start_time}} at {{facility}}. Reply STOP to opt out.",
    },
    {
        "kind": "sms_surgery_reminder",
        "label": "SMS — surgery reminder",
        "body": "WWC Gyn: Reminder — your surgery is in {{days_until}} days ({{surgery_date}} at {{start_time}}). Reply STOP to opt out.",
    },
    {
        "kind": "sms_generic_message",
        "label": "SMS — staff-composed message",
        "body": "WWC Gyn: {{body}} Reply STOP to opt out.",
    },
    {
        "kind": "sms_portal_login_code",
        "label": "SMS — portal sign-in code",
        "body": "WWC: Your portal sign-in code is {{code}}. Expires in 5 minutes. Reply STOP to opt out.",
    },
]


def seed_default_sms_templates(db: Session) -> int:
    """Idempotent: insert only kinds that don't already have a row."""
    inserted = 0
    for t in DEFAULT_SMS_TEMPLATES:
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


def seed_default_facilities(db: Session) -> int:
    inserted = 0
    for f in DEFAULT_FACILITIES:
        exists = db.query(Facility).filter(Facility.code == f["code"]).first()
        if exists:
            continue
        db.add(Facility(**f, created_by="seed", updated_by="seed"))
        inserted += 1
    if inserted:
        db.commit()
    return inserted
