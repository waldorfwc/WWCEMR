"""Notify the surgery coordinator (surgery@waldorfwomenscare.com) when
a patient takes an action via the portal or signs / declines a consent
via BoldSign.

Channels:
  - Email always (to SURGERY_SCHEDULER_NOTIFY_EMAIL, default
    surgery@waldorfwomenscare.com)
  - SMS to SURGERY_ONCALL_PHONE on top of the email when the event is a
    cancellation within 48h of the scheduled surgery date

Idempotent: each (surgery_id, event_kind, event_id) is recorded in
surgery_scheduler_notices, so a re-delivered BoldSign webhook can't
re-email the practice. event_id is a stable string — for portal
actions we synthesize one from the surgery id + an ISO timestamp; for
BoldSign-driven events we use the BoldSign envelope id + status.

All channels fail soft — a Slack/SMTP outage must not break the
patient action. Logged at WARN; the next event (or a manual replay)
will deliver.
"""
from __future__ import annotations

import logging
import os
from datetime import date as _date, datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgerySchedulerNotice

log = logging.getLogger(__name__)


# ─── Kill switch + config ─────────────────────────────────────────────

def _disabled() -> bool:
    return (os.environ.get("SURGERY_NOTIFY_DISABLE", "")
              .strip().lower() == "true")


def _scheduler_email() -> str:
    return (os.environ.get("SURGERY_SCHEDULER_NOTIFY_EMAIL", "")
              .strip() or "surgery@waldorfwomenscare.com")


def _oncall_phone() -> str:
    return os.environ.get("SURGERY_ONCALL_PHONE", "").strip()


# ─── Public entry point ───────────────────────────────────────────────

def notify_scheduler(
    db: Session,
    *,
    event_kind: str,
    surgery: Surgery,
    event_id: str,
    extra: Optional[dict] = None,
) -> None:
    """Send the scheduler notification for one patient event.

    event_kind: 'date_picked' | 'rescheduled' | 'cancelled' |
                'consent_signed' | 'consent_declined'
    event_id:   stable string per logical event (idempotency key)
    extra:      event-specific context — prev_date, fee_required,
                refund_required, envelope_id, decline_reason, etc.
    """
    if _disabled():
        log.info("Surgery scheduler notify disabled — skipping %s for %s",
                 event_kind, surgery.id)
        return

    # Idempotency: if we've already recorded this exact event, skip.
    existing = (db.query(SurgerySchedulerNotice)
                  .filter(SurgerySchedulerNotice.surgery_id == surgery.id,
                          SurgerySchedulerNotice.event_kind == event_kind,
                          SurgerySchedulerNotice.event_id == event_id)
                  .first())
    if existing is not None:
        log.info("Surgery scheduler notify already sent: %s/%s/%s",
                 surgery.id, event_kind, event_id)
        return

    channels_sent: list[str] = []
    extra = extra or {}

    # Email always.
    try:
        if _send_email(surgery, event_kind, extra):
            channels_sent.append("email")
    except Exception as exc:
        log.exception("Surgery scheduler email send raised: %s", exc)

    # SMS on top, only for same-week cancellations.
    if event_kind == "cancelled" and _is_within_48h(surgery):
        try:
            if _send_sms(surgery, extra):
                channels_sent.append("sms")
        except Exception as exc:
            log.exception("Surgery scheduler SMS send raised: %s", exc)

    if not channels_sent:
        log.warning(
            "Surgery scheduler notify: NO channels delivered for "
            "%s/%s/%s — leaving idempotency row unwritten so the next "
            "delivery attempt can replay",
            surgery.id, event_kind, event_id)
        return

    # Record the successful send. Use a try/except around commit so a
    # race with another worker (same event arriving twice) ends in the
    # unique-constraint violation, not a 500.
    notice = SurgerySchedulerNotice(
        surgery_id=surgery.id,
        event_kind=event_kind,
        event_id=event_id,
        channels=",".join(channels_sent),
        detail=str(extra) if extra else None,
    )
    db.add(notice)
    try:
        db.commit()
    except IntegrityError:
        # Another worker beat us to it — the actual notification was
        # already sent in their codepath. Roll back our own row.
        db.rollback()
        log.info("Surgery scheduler notice already recorded — race resolved")


# ─── Channels ─────────────────────────────────────────────────────────

def _send_email(surgery: Surgery, event_kind: str, extra: dict) -> bool:
    from app.services.checklist_notifications import send_email
    to = _scheduler_email()
    if not to:
        return False
    subject = _email_subject(surgery, event_kind)
    text_body, html_body = _email_body(surgery, event_kind, extra)
    return bool(send_email(to=to, subject=subject,
                            html_body=html_body, text_body=text_body))


def _send_sms(surgery: Surgery, extra: dict) -> bool:
    phone = _oncall_phone()
    if not phone:
        log.info("Surgery scheduler SMS skipped — SURGERY_ONCALL_PHONE not set")
        return False
    # Reuse the same Twilio path checklist notifications use.
    from app.services.checklist_notifications import send_sms
    name = _patient_name(surgery)
    when = _surgery_when(surgery)
    fee = " (within-14d FEE)" if extra.get("fee_required") else ""
    refund = " (refund needed)" if extra.get("refund_required") else ""
    body = (f"[WWC] Same-week cancel: {name} {when}{fee}{refund}. "
            f"Open the surgery dashboard to process.")
    try:
        return bool(send_sms(to_phone=phone, body=body[:240]))
    except Exception as exc:
        log.warning("send_sms failed: %s", exc)
        return False


# ─── Formatters ───────────────────────────────────────────────────────

def _patient_name(s: Surgery) -> str:
    parts = [s.first_name or "", s.last_name or ""]
    n = " ".join(p for p in parts if p).strip()
    return n or (s.email or s.id_pretty() if hasattr(s, "id_pretty") else "<unnamed>")


def _surgery_when(s: Surgery) -> str:
    if s.scheduled_date:
        return s.scheduled_date.strftime("%m/%d/%Y")
    return "(no scheduled date)"


def _email_subject(s: Surgery, event_kind: str) -> str:
    label = {
        "date_picked":      "Date picked",
        "rescheduled":      "Rescheduled",
        "cancelled":        "Cancelled",
        "consent_signed":   "Consent signed",
        "consent_declined": "Consent declined",
    }.get(event_kind, event_kind)
    return f"[Surgery] {label} — {_patient_name(s)}"


def _email_body(s: Surgery, event_kind: str, extra: dict) -> tuple[str, str]:
    name = _patient_name(s)
    when = _surgery_when(s)
    chart = s.chart_number or "(no chart#)"
    surgeon = s.surgeon_email or "(unassigned)"

    lines = [
        f"Patient: {name}",
        f"Chart:   {chart}",
        f"When:    {when}",
        f"Surgeon: {surgeon}",
        f"Surgery: {s.id}",
    ]
    if event_kind == "rescheduled" and extra.get("prev_date"):
        lines.insert(3, f"Prev:    {extra['prev_date']}")
    if event_kind == "cancelled":
        if extra.get("fee_required"):
            lines.append("⚠️  Within-14-day window: $351 cancellation fee applies.")
        if extra.get("refund_required"):
            lines.append("⚠️  Refund processing required.")
        if extra.get("reason"):
            lines.append(f"Patient reason: {extra['reason']}")
    if event_kind == "consent_declined" and extra.get("decline_reason"):
        lines.append(f"BoldSign decline reason: {extra['decline_reason']}")
    if event_kind == "consent_signed" and extra.get("all_signed") is True:
        lines.append("✅ All required consent envelopes are now signed.")

    text = "\n".join(lines) + "\n"
    html_lines = [f"<p><strong>{name}</strong> &mdash; {chart}</p>", "<ul>"]
    for ln in lines:
        if ":" in ln:
            k, v = ln.split(":", 1)
            html_lines.append(f"<li><strong>{k.strip()}:</strong> {v.strip()}</li>")
        else:
            html_lines.append(f"<li>{ln}</li>")
    html_lines.append("</ul>")
    html = "\n".join(html_lines)
    return text, html


def _is_within_48h(s: Surgery) -> bool:
    if not s.scheduled_date:
        return False
    return (s.scheduled_date - _date.today()) <= timedelta(days=2)
