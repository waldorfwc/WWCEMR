"""Patient transactional SMS — render template + Twilio send + audit + consent.

Soft-fail mirrors patient_email. Additionally, every send checks
Surgery.sms_consent — if False, returns a 'skipped' PatientSms row with
failure_reason="patient has not opted in to SMS".
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.patient_sms import (
    SmsTemplate, PatientSms, SMS_TEMPLATE_KINDS, PATIENT_SMS_STATUSES,
)
from app.models.surgery import Surgery
from app.services.checklist_notifications import send_sms
from app.services.surgery_klara_drafter import FACILITY_SHORT

log = logging.getLogger(__name__)


_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render(body: str, context: dict) -> str:
    def _repl(m):
        return str(context.get(m.group(1), ""))
    return _VAR_RE.sub(_repl, body)


def build_sms_context(surgery: Surgery, **extras) -> dict:
    """Standard SMS template context for a Surgery.

    Fills the variables used across the four seeded templates:
        patient_name, surgery_date, surgery_time, facility_name, practice_phone

    `extras` are merged in (kind-specific values like amount / payment_link /
    message), so the caller can write a single call:

        ctx = build_sms_context(surgery,
                                amount="$250.00",
                                payment_link=link)
        send_patient_sms(db, kind="sms_payment_link",
                         surgery=surgery, context=ctx, sent_by=user.email)

    `practice_phone` comes from the WWC_PRACTICE_PHONE env var. If unset, the
    var renders as an empty string — visible in QA but not a runtime error.
    """
    first = (surgery.first_name
             or (surgery.patient_name or "").split(",")[-1].strip().split(" ")[0]
             or "")

    surgery_date = ""
    if surgery.scheduled_date:
        # "Wed Jun 3" — terse for SMS, drops the year
        surgery_date = surgery.scheduled_date.strftime("%a %b %-d")

    surgery_time = ""
    if surgery.scheduled_start_time:
        # "7:30 AM" — strip leading zero on hour for shorter SMS
        surgery_time = surgery.scheduled_start_time.strftime("%-I:%M %p")

    facility_name = ""
    if surgery.selected_facility:
        facility_name = FACILITY_SHORT.get(
            surgery.selected_facility, surgery.selected_facility
        )

    # Single "when" string so templates don't have to glue date+time
    # themselves. Time is optional — when blank, surgery_when is just
    # the date so the rendered SMS doesn't end up with "at ," literals.
    surgery_when = surgery_date
    if surgery_date and surgery_time:
        surgery_when = f"{surgery_date} at {surgery_time}"
    elif surgery_time:
        surgery_when = surgery_time

    ctx = {
        "patient_name":   first,
        "surgery_date":   surgery_date,
        "surgery_time":   surgery_time,
        "surgery_when":   surgery_when,
        "facility_name":  facility_name,
        "practice_phone": os.environ.get("WWC_PRACTICE_PHONE", "").strip(),
    }
    ctx.update(extras)
    return ctx


def _segments(text: str) -> int:
    """Twilio segment count: 160 chars per single-segment SMS (GSM-7
    encoding). Multi-segment messages use 153 chars per segment because
    of UDH overhead. Cheap approximation — Twilio's actual count may
    differ for emojis/Unicode."""
    if len(text) <= 160:
        return 1
    return (len(text) + 152) // 153


def send_patient_sms(
    db: Session,
    *,
    kind: Optional[str],
    surgery: Optional[Surgery],
    context: dict,
    sent_by: str,
    to_phone: Optional[str] = None,
    chart_number: Optional[str] = None,
    ad_hoc_body: Optional[str] = None,
) -> PatientSms:
    """Send a patient SMS and write the audit row.

    Modes:
      Template send  — pass `kind`. Body comes from SmsTemplate and is
                       rendered with `context`.
      Ad-hoc send    — pass `kind=None` + `ad_hoc_body`.

    The `surgery` argument is required for template sends (to look up
    consent + cell_phone). For ad-hoc, pass a Surgery row if you have
    one, or None + explicit `to_phone` + `chart_number` to bypass.
    """
    # Resolve recipient
    phone = to_phone or (surgery.cell_phone if surgery else None)
    phone = (phone or "").strip()

    # Consent gate
    if surgery is not None and not surgery.sms_consent:
        return _record(db,
            surgery_id=(surgery.id if surgery else None),
            chart_number=chart_number or (surgery.chart_number if surgery else None),
            to_phone=phone or "(missing)",
            kind=kind, body="(unsent — no consent)",
            status="skipped",
            failure_reason="patient has not opted in to SMS",
            sent_by=sent_by, context=context, segments=None, twilio_sid=None)

    # Resolve body
    if kind:
        template = (db.query(SmsTemplate)
                      .filter(SmsTemplate.kind == kind,
                               SmsTemplate.is_active.is_(True))
                      .first())
        if template is None:
            return _record(db,
                surgery_id=(surgery.id if surgery else None),
                chart_number=chart_number or (surgery.chart_number if surgery else None),
                to_phone=phone or "(missing)",
                kind=kind, body="(unrendered)",
                status="skipped",
                failure_reason=f"no active template for kind={kind}",
                sent_by=sent_by, context=context, segments=None, twilio_sid=None)
        body = render(template.body, context)
    else:
        if not ad_hoc_body:
            return _record(db,
                surgery_id=(surgery.id if surgery else None),
                chart_number=chart_number or (surgery.chart_number if surgery else None),
                to_phone=phone or "(missing)",
                kind=None, body="(missing)",
                status="skipped",
                failure_reason="ad-hoc send missing body",
                sent_by=sent_by, context=context, segments=None, twilio_sid=None)
        body = render(ad_hoc_body, context)

    if not phone:
        return _record(db,
            surgery_id=(surgery.id if surgery else None),
            chart_number=chart_number or (surgery.chart_number if surgery else None),
            to_phone="(missing)",
            kind=kind, body=body,
            status="skipped",
            failure_reason="recipient phone number is blank",
            sent_by=sent_by, context=context, segments=None, twilio_sid=None)

    segments = _segments(body)
    try:
        sid = send_sms(phone, body)
    except Exception as e:
        log.warning("patient sms Twilio raised: %s", e)
        sid = None
    status = "sent" if sid is not None else "failed"
    failure_reason = None if sid is not None else "Twilio send returned None (check TWILIO_* config)"

    return _record(db,
        surgery_id=(surgery.id if surgery else None),
        chart_number=chart_number or (surgery.chart_number if surgery else None),
        to_phone=phone, kind=kind, body=body,
        status=status, failure_reason=failure_reason,
        sent_by=sent_by, context=context,
        segments=str(segments), twilio_sid=(sid or None),
    )


def _record(db, *, surgery_id, chart_number, to_phone, kind, body,
            status, failure_reason, sent_by, context, segments, twilio_sid) -> PatientSms:
    row = PatientSms(
        surgery_id=surgery_id, chart_number=chart_number,
        to_phone=to_phone, template_kind=kind,
        rendered_body=body, status=status,
        failure_reason=failure_reason, sent_by=sent_by,
        context=context or {}, segments=segments, twilio_sid=twilio_sid,
    )
    db.add(row)
    db.commit(); db.refresh(row)
    return row
