"""Seed the four SmsTemplate rows that patient_sms.send_patient_sms looks up
by `kind`. Idempotent on `kind` — updates if a row already exists.

Run once after Phase J deploy:

    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/seed_sms_templates.py

Each template stays under 160 characters where possible (1 Twilio segment =
cheaper). All include the WWC sender identifier and a STOP opt-out per TCPA
guidance for transactional SMS.

Available {{vars}} (caller fills via context):
  patient_name    — first name preferred (terser)
  surgery_date    — formatted, e.g. "Wed Jun 3"
  surgery_time    — formatted, e.g. "7:30 AM"
  facility_name   — short label, e.g. "MedStar SMD" / "WWC Office"
  payment_link    — Stripe Checkout URL
  amount          — dollar amount, e.g. "$250.00"
  practice_phone  — WWC callback number, e.g. "(301) 638-5511"
  message         — free-text body for sms_generic_message
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.patient_sms import SmsTemplate, SMS_TEMPLATE_KINDS
# Side-effect imports for SQLAlchemy mapper config
from app.models import stripe_payment   # noqa: F401
from app.models import patient_email    # noqa: F401
from app.models.surgery import Surgery  # noqa: F401


# (kind, label, body)
TEMPLATES = [
    ("sms_payment_link",
     "Payment link",
     "WWC: Your surgery deposit of {{amount}} is ready. "
     "Pay securely: {{payment_link}} "
     "Questions? {{practice_phone}}. Reply STOP to opt out."),

    ("sms_surgery_confirmation",
     "Surgery confirmation",
     "WWC: Surgery confirmed for {{surgery_date}} at {{surgery_time}}, "
     "{{facility_name}}. Arrive 1 hr early. "
     "Questions? {{practice_phone}}. Reply STOP."),

    ("sms_surgery_reminder",
     "Surgery reminder (day before)",
     "WWC reminder: Surgery {{surgery_date}} at {{surgery_time}}, "
     "{{facility_name}}. Nothing to eat/drink after midnight. "
     "Questions? {{practice_phone}}. Reply STOP."),

    ("sms_generic_message",
     "Generic message",
     "WWC: {{message}} "
     "Questions? Call {{practice_phone}}. Reply STOP to opt out."),

    ("sms_portal_login_code",
     "Portal sign-in code",
     "WWC: Your portal sign-in code is {{code}}. "
     "Expires in 5 minutes. Reply STOP to opt out."),
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)

    # Sanity: every TEMPLATES kind must be in SMS_TEMPLATE_KINDS
    kinds_in_code = set(SMS_TEMPLATE_KINDS)
    kinds_in_seed = {t[0] for t in TEMPLATES}
    extra = kinds_in_seed - kinds_in_code
    missing = kinds_in_code - kinds_in_seed
    if extra or missing:
        print(f"ERROR: TEMPLATES vs SMS_TEMPLATE_KINDS mismatch", file=sys.stderr)
        print(f"  extra (in seed only):   {sorted(extra)}", file=sys.stderr)
        print(f"  missing (in code only): {sorted(missing)}", file=sys.stderr)
        sys.exit(3)

    eng = create_engine(db_url)
    Session = sessionmaker(bind=eng)
    sess = Session()

    created = 0
    updated = 0
    for kind, label, body in TEMPLATES:
        row = (sess.query(SmsTemplate)
                  .filter(SmsTemplate.kind == kind).first())
        if row is None:
            sess.add(SmsTemplate(
                kind=kind, label=label, body=body,
                is_active=True, updated_by="seed",
            ))
            created += 1
            print(f"  + {kind:32s}  {len(body)} chars")
        else:
            row.label = label
            row.body = body
            row.is_active = True
            row.updated_by = "seed"
            updated += 1
            print(f"  ~ {kind:32s}  {len(body)} chars")
    sess.commit()

    print()
    print(f"Created: {created}")
    print(f"Updated: {updated}")


if __name__ == "__main__":
    main()
