"""Seed the seven EmailTemplate rows that patient_email.send_patient_email
looks up by `kind`. Idempotent on `kind` (UniqueConstraint).

Run once after Phase I deploy:

    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/seed_email_templates.py

Each template renders consistent HTML (inline styles for Gmail/Outlook
reliability — no external CSS, no images) and includes a plain-text
fallback for clients that strip HTML.

Available {{vars}} are documented per-kind below. The dispatch sites
each fill the ones they need; missing vars render as empty strings.
The shared footer uses {{practice_phone}} — populate via the
WWC_PRACTICE_PHONE env var or pass it in the context.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.patient_email import EmailTemplate, EMAIL_TEMPLATE_KINDS
# Side-effect imports so SQLAlchemy can resolve cross-module relationships
from app.models import stripe_payment   # noqa: F401
from app.models import patient_sms      # noqa: F401
from app.models.surgery import Surgery  # noqa: F401


# Shared wrappers ---------------------------------------------------------

# `body_html` slot holds the per-template content. Footer is identical
# across all kinds so coordinators can update one place (this seed script)
# rather than 7 individual bodies.
WRAP = """\
<div style="font-family: Helvetica, Arial, sans-serif; font-size: 14px;
            color: #1f2937; line-height: 1.5; max-width: 560px;
            margin: 0 auto; padding: 16px;">
  <p style="font-size: 18px; font-weight: 600; color: #4c1d95; margin: 0 0 12px;">
    WWC Gynecology &amp; Aesthetics
  </p>
  {body_html}
  <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0 12px;">
  <p style="font-size: 12px; color: #6b7280; margin: 0;">
    Questions? Call our office at {{practice_phone}}. This message was sent
    from an unmonitored mailbox — please don't reply.
  </p>
</div>
"""

TEXT_FOOTER = (
    "\n\n—\nWWC Gynecology & Aesthetics\n"
    "Questions? Call our office at {{practice_phone}}.\n"
    "This message was sent from an unmonitored mailbox — please don't reply."
)


# Per-template definitions -----------------------------------------------

# (kind, label, subject, body_html_inner, text_body)

TEMPLATES = [
    # ── stripe_payment_link ─────────────────────────────────────────────
    # Vars: patient_name, amount, checkout_url
    (
        "stripe_payment_link",
        "Payment link",
        "WWC: Payment link for your upcoming surgery",
        """
        <p>Hi {{patient_name}},</p>
        <p>Your surgery deposit of <strong>${{amount}}</strong> is ready
        for payment. Click below to pay securely through Stripe:</p>
        <p style="margin: 20px 0;">
          <a href="{{checkout_url}}"
             style="background: #4c1d95; color: #fff; padding: 10px 18px;
                    border-radius: 6px; text-decoration: none;
                    display: inline-block;">Pay now</a>
        </p>
        <p style="font-size: 12px; color: #6b7280;">
          Or copy this link into your browser:<br>
          {{checkout_url}}
        </p>
        """,
        "Hi {{patient_name}},\n\n"
        "Your surgery deposit of ${{amount}} is ready for payment.\n\n"
        "Pay securely: {{checkout_url}}",
    ),

    # ── stripe_payment_receipt ──────────────────────────────────────────
    # Vars: patient_name, amount, surgery_date
    (
        "stripe_payment_receipt",
        "Payment receipt",
        "WWC: Payment received — ${{amount}}",
        """
        <p>Hi {{patient_name}},</p>
        <p>We've received your payment of <strong>${{amount}}</strong>
        toward your surgery scheduled for <strong>{{surgery_date}}</strong>.
        Thank you!</p>
        <p>Please keep this email for your records. If you have questions
        about your account, call our office.</p>
        """,
        "Hi {{patient_name}},\n\n"
        "We've received your payment of ${{amount}} toward your surgery\n"
        "scheduled for {{surgery_date}}. Thank you!\n\n"
        "Please keep this email for your records.",
    ),

    # ── surgery_confirmation ────────────────────────────────────────────
    # Vars: patient_name, surgery_date, start_time, facility, procedure
    (
        "surgery_confirmation",
        "Surgery confirmation",
        "WWC: Surgery confirmed for {{surgery_date}}",
        """
        <p>Hi {{patient_name}},</p>
        <p>Your surgery is confirmed. Please review the details:</p>
        <table style="border-collapse: collapse; margin: 12px 0;">
          <tr><td style="padding: 4px 12px 4px 0; color: #6b7280;">Procedure</td>
              <td><strong>{{procedure}}</strong></td></tr>
          <tr><td style="padding: 4px 12px 4px 0; color: #6b7280;">Date</td>
              <td><strong>{{surgery_date}}</strong></td></tr>
          <tr><td style="padding: 4px 12px 4px 0; color: #6b7280;">Arrival time</td>
              <td><strong>{{start_time}}</strong></td></tr>
          <tr><td style="padding: 4px 12px 4px 0; color: #6b7280;">Location</td>
              <td><strong>{{facility}}</strong></td></tr>
        </table>
        <p>Please arrive at the time above. We'll send a separate reminder
        the day before with pre-op instructions.</p>
        """,
        "Hi {{patient_name}},\n\n"
        "Your surgery is confirmed:\n\n"
        "  Procedure:     {{procedure}}\n"
        "  Date:          {{surgery_date}}\n"
        "  Arrival time:  {{start_time}}\n"
        "  Location:      {{facility}}\n\n"
        "We'll send a separate reminder the day before with pre-op instructions.",
    ),

    # ── surgery_reminder ────────────────────────────────────────────────
    # Vars: patient_name, surgery_date, start_time, facility, procedure, days_until
    (
        "surgery_reminder",
        "Surgery reminder (pre-op)",
        "WWC: Reminder — surgery in {{days_until}} day(s)",
        """
        <p>Hi {{patient_name}},</p>
        <p>This is a reminder that your <strong>{{procedure}}</strong> is
        scheduled in <strong>{{days_until}} day(s)</strong> — on
        <strong>{{surgery_date}}</strong> at <strong>{{start_time}}</strong>
        at {{facility}}.</p>
        <p><strong>Important pre-op reminders:</strong></p>
        <ul style="padding-left: 20px;">
          <li>No food or drink after midnight the night before.</li>
          <li>Take only the medications your provider approved.</li>
          <li>Arrange a ride home — you cannot drive after surgery.</li>
        </ul>
        <p>If anything changes or you have questions, call our office today.</p>
        """,
        "Hi {{patient_name}},\n\n"
        "This is a reminder that your {{procedure}} is scheduled in\n"
        "{{days_until}} day(s) — {{surgery_date}} at {{start_time}}, {{facility}}.\n\n"
        "Important pre-op reminders:\n"
        "  - No food or drink after midnight the night before.\n"
        "  - Take only the medications your provider approved.\n"
        "  - Arrange a ride home — you cannot drive after surgery.",
    ),

    # ── docusign_consent_sent ───────────────────────────────────────────
    # Note: name is legacy "docusign_…" but the dispatch site fires for
    # BOTH DocuSign and BoldSign consent sends (per the BoldSign-coexist
    # decision). Generic copy that works either way.
    # Vars: patient_name, surgery_date
    (
        "docusign_consent_sent",
        "Consent forms sent",
        "WWC: Action needed — please sign your consent forms",
        """
        <p>Hi {{patient_name}},</p>
        <p>We've sent the consent forms for your surgery scheduled
        <strong>{{surgery_date}}</strong>. You should receive a separate
        email from our e-signature provider with a link to review and
        sign each form.</p>
        <p><strong>Please complete the signatures within 7 days.</strong> If you don't
        see the email, check your spam folder first, then call us.</p>
        """,
        "Hi {{patient_name}},\n\n"
        "We've sent the consent forms for your surgery on {{surgery_date}}.\n"
        "You should receive a separate email from our e-signature provider\n"
        "with a link to review and sign each form.\n\n"
        "Please complete the signatures within 7 days. If you don't see\n"
        "the email, check your spam folder first, then call us.",
    ),

    # ── generic_patient_message ────────────────────────────────────────
    # Coordinator compose endpoint (routers/surgery.py). The `body` var
    # is HTML composed by the coordinator and inserted verbatim; we only
    # add the WWC wrapper + footer around it.
    # Vars: patient_name, subject, body, sender_name
    (
        "generic_patient_message",
        "Generic message (coordinator compose)",
        "{{subject}}",
        """
        <p>Hi {{patient_name}},</p>
        {{body}}
        <p style="margin-top: 20px;">— {{sender_name}}<br>
          <span style="color: #6b7280;">Waldorf Women's Care</span></p>
        """,
        "Hi {{patient_name}},\n\n"
        "{{body}}\n\n"
        "— {{sender_name}}\n"
        "Waldorf Women's Care",
    ),

    # ── surgery_post_op_followup ───────────────────────────────────────
    # No dispatch site yet; defined as a future kind. Seed a sensible
    # body so that when the dispatch is wired, the message reads well.
    # Vars: patient_name
    (
        "surgery_post_op_followup",
        "Post-op follow-up check-in",
        "WWC: How are you feeling after your procedure?",
        """
        <p>Hi {{patient_name}},</p>
        <p>It's been a few days since your procedure with us. How are you
        feeling? Most patients recover quickly, but if you're experiencing
        any of the following, please call our office right away:</p>
        <ul style="padding-left: 20px;">
          <li>Fever over 100.4°F</li>
          <li>Heavy bleeding (soaking a pad in less than an hour)</li>
          <li>Severe pain not controlled by your prescribed medication</li>
          <li>Signs of infection at any incision site</li>
        </ul>
        <p>Your post-op visit is part of your care plan. If you haven't
        scheduled it yet, please call to set it up.</p>
        """,
        "Hi {{patient_name}},\n\n"
        "It's been a few days since your procedure. How are you feeling?\n\n"
        "Please call our office right away if you're experiencing:\n"
        "  - Fever over 100.4°F\n"
        "  - Heavy bleeding (soaking a pad in less than an hour)\n"
        "  - Severe pain not controlled by your prescribed medication\n"
        "  - Signs of infection at any incision site\n\n"
        "Your post-op visit is part of your care plan. If you haven't\n"
        "scheduled it yet, please call to set it up.",
    ),
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)

    # Sanity: every TEMPLATES kind must be in EMAIL_TEMPLATE_KINDS
    kinds_in_code = set(EMAIL_TEMPLATE_KINDS)
    kinds_in_seed = {t[0] for t in TEMPLATES}
    extra = kinds_in_seed - kinds_in_code
    missing = kinds_in_code - kinds_in_seed
    if extra or missing:
        print("ERROR: TEMPLATES vs EMAIL_TEMPLATE_KINDS mismatch", file=sys.stderr)
        print(f"  extra (in seed only):   {sorted(extra)}", file=sys.stderr)
        print(f"  missing (in code only): {sorted(missing)}", file=sys.stderr)
        sys.exit(3)

    eng = create_engine(db_url)
    Session = sessionmaker(bind=eng)
    sess = Session()

    created = 0
    updated = 0
    for kind, label, subject, body_inner, text_body in TEMPLATES:
        html = WRAP.format(body_html=body_inner.strip())
        text = (text_body.rstrip() + TEXT_FOOTER)
        row = (sess.query(EmailTemplate)
                  .filter(EmailTemplate.kind == kind).first())
        if row is None:
            sess.add(EmailTemplate(
                kind=kind, label=label, subject=subject,
                html_body=html, text_body=text,
                is_active=True, updated_by="seed",
            ))
            created += 1
            print(f"  + {kind:30s}  subject_len={len(subject)}  "
                  f"html_len={len(html)}  text_len={len(text)}")
        else:
            row.label     = label
            row.subject   = subject
            row.html_body = html
            row.text_body = text
            row.is_active = True
            row.updated_by = "seed"
            updated += 1
            print(f"  ~ {kind:30s}  subject_len={len(subject)}  "
                  f"html_len={len(html)}  text_len={len(text)}")
    sess.commit()

    print()
    print(f"Created: {created}")
    print(f"Updated: {updated}")


if __name__ == "__main__":
    main()
