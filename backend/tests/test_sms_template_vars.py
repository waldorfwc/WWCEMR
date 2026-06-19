"""Regression: seeded SMS templates must only reference variables the dispatch
actually provides — otherwise patients get blank fields. Real bug fixed here:
- sms_generic_message used {{body}} but /send-patient-sms passes `message`
  → patients received "WWC Gyn:  Reply STOP to opt out."
- sms_surgery_confirmation / _reminder used {{start_time}}/{{facility}} but
  build_sms_context provides surgery_time/facility_name → blank time + place.
"""
import re

from sqlalchemy import text

from app.models.patient_sms import SmsTemplate
from app.models.surgery import Surgery
from app.services.patient_sms import build_sms_context
from app.services.surgery.config_seed import DEFAULT_SMS_TEMPLATES


# Per-kind extras each dispatch site merges into build_sms_context(...).
_EXTRAS = {
    "sms_payment_link":         {"amount", "checkout_url", "payment_link"},
    "sms_surgery_confirmation": set(),
    "sms_surgery_reminder":     {"days_until"},
    "sms_generic_message":      {"message"},
    "sms_portal_login_code":    {"code"},
}


def _vars(body):
    return set(re.findall(r"\{\{\s*(\w+)\s*\}\}", body))


def test_seeded_sms_templates_only_reference_provided_vars(db):
    base = set(build_sms_context(Surgery(chart_number="1", patient_name="Doe, J")).keys())
    bad = {}
    for t in DEFAULT_SMS_TEMPLATES:
        allowed = base | _EXTRAS.get(t["kind"], set())
        missing = _vars(t["body"]) - allowed
        if missing:
            bad[t["kind"]] = missing
    assert not bad, f"SMS templates reference vars the dispatch never provides: {bad}"


def test_sms_template_var_migration_fixes_and_is_idempotent(db):
    # Seed rows with the OLD broken placeholders, then run the migration SQL.
    db.add(SmsTemplate(kind="sms_generic_message", label="x",
                       body="WWC Gyn: {{body}} Reply STOP to opt out."))
    db.add(SmsTemplate(kind="sms_surgery_confirmation", label="x",
                       body="confirmed for {{surgery_date}} at {{start_time}} at {{facility}}."))
    db.commit()

    stmts = (
        "UPDATE sms_templates SET body = REPLACE(body, '{{body}}', '{{message}}') WHERE body LIKE '%{{body}}%'",
        "UPDATE sms_templates SET body = REPLACE(body, '{{start_time}}', '{{surgery_time}}') WHERE body LIKE '%{{start_time}}%'",
        "UPDATE sms_templates SET body = REPLACE(body, '{{facility}}', '{{facility_name}}') WHERE body LIKE '%{{facility}}%'",
    )
    for _ in range(2):                       # run twice → idempotent
        for sql in stmts:
            db.execute(text(sql))
        db.commit()

    generic = db.query(SmsTemplate).filter_by(kind="sms_generic_message").one()
    conf = db.query(SmsTemplate).filter_by(kind="sms_surgery_confirmation").one()
    assert "{{message}}" in generic.body and "{{body}}" not in generic.body
    assert "{{surgery_time}}" in conf.body and "{{start_time}}" not in conf.body
    assert "{{facility_name}}" in conf.body and "{{facility}}" not in conf.body
