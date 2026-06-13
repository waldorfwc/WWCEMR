"""F9 audit fixes (#21 MM/DD/YYYY reminder date, #22 idempotency excludes
'skipped', #23 seed money $50K clamp)."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from app.models.surgery import Surgery
from app.models.patient_email import EmailTemplate, PatientEmail
from app.services.surgery.reminders import _already_sent_for, run_reminder_sweep
from app.services.surgery.smartsheet_seed import _money


# ─── #23 — money $50K clamp ──────────────────────────────────────────

def test_money_clamps_above_ceiling():
    assert _money("75000") == Decimal("0")


def test_money_passes_normal_value():
    assert _money("1200.50") == Decimal("1200.50")


def test_money_handles_blank_and_none():
    assert _money("") is None
    assert _money(None) is None


def test_money_at_ceiling_not_clamped():
    # exactly $50K is the boundary — kept (clamp is strictly > ceiling)
    assert _money("50000") == Decimal("50000")


# ─── #22 — idempotency excludes status='skipped' ─────────────────────

def _seed_surgery(db, days_out, email="pat@example.com"):
    s = Surgery(
        chart_number="1", patient_name="Pat", email=email,
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="confirmed",
        scheduled_date=date.today() + timedelta(days=days_out),
        procedures=[{"name": "Hyst", "kind": "robotic_180"}],
    )
    db.add(s); db.flush()
    return s


def _seed_reminder_row(db, surgery, lead, status):
    db.add(PatientEmail(
        surgery_id=surgery.id,
        chart_number=surgery.chart_number,
        to_email=surgery.email or "(missing)",
        template_kind="surgery_reminder",
        rendered_subject="x", rendered_html="<p>x</p>",
        status=status,
        context={"days_until": str(lead)},
    ))
    db.flush()


def test_already_sent_ignores_skipped_row(db):
    s = _seed_surgery(db, 3)
    _seed_reminder_row(db, s, 3, status="skipped")
    db.commit()
    # A 'skipped' row must NOT suppress the next real send.
    assert _already_sent_for(db, s.id, 3) is False


def test_already_sent_counts_sent_row(db):
    s = _seed_surgery(db, 3)
    _seed_reminder_row(db, s, 3, status="sent")
    db.commit()
    assert _already_sent_for(db, s.id, 3) is True


def test_skipped_row_does_not_block_later_real_send(db):
    """A blank-email patient produced a 'skipped' row; after the email is
    filled in, the next sweep must still send."""
    db.add(EmailTemplate(
        kind="surgery_reminder", label="x",
        subject="Reminder: {{days_until}} days",
        html_body="<p>Hi {{patient_name}}, surgery on {{surgery_date}}</p>",
    ))
    s = _seed_surgery(db, 3)
    _seed_reminder_row(db, s, 3, status="skipped")
    db.commit()

    with patch("app.services.patient_email.send_email", return_value=True):
        out = run_reminder_sweep(db)
    assert out["sent"] == 1
    assert out["skipped"] == 0


# ─── #21 — reminder email date renders MM/DD/YYYY ────────────────────

def test_reminder_email_context_uses_mmddyyyy(db):
    db.add(EmailTemplate(
        kind="surgery_reminder", label="x",
        subject="Reminder: {{days_until}} days",
        html_body="<p>Hi {{patient_name}}, surgery on {{surgery_date}}</p>",
    ))
    s = _seed_surgery(db, 3)
    db.commit()
    target = date.today() + timedelta(days=3)

    captured = {}

    def _capture(db_, *, kind, to_email, context, **kwargs):
        captured.update(context)

    with patch("app.services.surgery.reminders.send_patient_email", side_effect=_capture), \
         patch("app.services.surgery.reminders._sms_already_sent_for", return_value=True):
        run_reminder_sweep(db)

    assert captured["surgery_date"] == target.strftime("%m/%d/%Y")
    # not ISO
    assert captured["surgery_date"] != target.isoformat()
