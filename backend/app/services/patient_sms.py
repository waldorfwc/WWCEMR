"""Patient transactional SMS — render template + Twilio send + audit + consent.

Soft-fail mirrors patient_email. Additionally, every send checks
Surgery.sms_consent — if False, returns a 'skipped' PatientSms row with
failure_reason="patient has not opted in to SMS".
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from app.utils.dt import now_utc_naive
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.patient_sms import (
    SmsTemplate, PatientSms, SMS_TEMPLATE_KINDS, PATIENT_SMS_STATUSES,
)
from app.models.surgery import Surgery
from app.services.checklist_notifications import send_sms
from app.services.surgery.klara_drafter import FACILITY_SHORT, arrival_time_str

log = logging.getLogger(__name__)


_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render(body: str, context: dict) -> str:
    """Render {{var}} placeholders against `context`. Missing variables
    render as empty (template authors expect this) but are logged so a
    typo like {{paitent_name}} doesn't silently ship a blank to the
    patient with no signal. (Fable portal audit M4.)
    """
    log = logging.getLogger(__name__)
    missing: list[str] = []

    def _repl(m):
        key = m.group(1)
        if key not in context:
            missing.append(key)
            return ""
        return str(context[key])

    out = _VAR_RE.sub(_repl, body)
    if missing:
        log.warning("SMS template missing variables: %s", sorted(set(missing)))
    return out


# Twilio segment thresholds — GSM-7 (160/153 first/continuation) vs.
# UCS-2 / Unicode (70/67). Patient names with smart quotes / accents
# fall into UCS-2 in practice. (Fable portal audit M3.)
_GSM7_CHARS = (
    " \n\r"
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "!\"#$%&'()*+,-./:;<=>?@_¡¿"
    "ÄÅÆÇÉÑÖØÜßàäåæèéìñòöøùü"
    "ΔΦΓΛΩΠΨΣΘΞ"
    "£¤¥§"
    # GSM-7 extension table (counts as 2 chars each)
    "{}[]~^|€\\"
)
_GSM7_SET = frozenset(_GSM7_CHARS)


def _is_gsm7_only(text: str) -> bool:
    return all(c in _GSM7_SET for c in text)


def _extract_first_name(surgery: Surgery) -> str:
    """Best-effort first-name extraction. Prefers Surgery.first_name when
    set; otherwise parses patient_name. Handles "Last, First Middle"
    (canonical) and "First Last" (no comma) without producing the
    empty string on the trailing-comma edge. (Fable portal audit M6.)
    """
    if (surgery.first_name or "").strip():
        return surgery.first_name.strip()
    raw = (surgery.patient_name or "").strip()
    if not raw:
        return ""
    if "," in raw:
        # "Last, First Middle" — take what's after the LAST comma so
        # "Smith Jr., Mary" still extracts "Mary".
        after_comma = raw.rsplit(",", 1)[-1].strip()
        first = after_comma.split(" ")[0].strip() if after_comma else ""
        if first:
            return first
        # Trailing-comma edge ("Last, ") — fall through to first token of
        # the part BEFORE the comma.
        before_comma = raw.rsplit(",", 1)[0].strip()
        return before_comma.split(" ")[0].strip()
    # No comma: assume "First Last" — take first token.
    return raw.split(" ")[0].strip()


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
    first = _extract_first_name(surgery)

    surgery_date = ""
    if surgery.scheduled_date:
        # "Wed Jun 3" — terse for SMS, drops the year. lstrip the day
        # to be platform-portable (%-d is glibc-only). (Fable M1.)
        surgery_date = (
            surgery.scheduled_date.strftime("%a %b ")
            + str(surgery.scheduled_date.day))

    surgery_time = ""
    if surgery.scheduled_start_time:
        # "7:30 AM" — strip leading zero on hour for shorter SMS.
        # Same portability fix as surgery_date. (Fable M1.)
        t = surgery.scheduled_start_time
        h12 = ((t.hour - 1) % 12) + 1
        surgery_time = f"{h12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"

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

    # Patient-facing arrival time (24h source → "1:30 PM"-style render).
    # Hospitals = surgery − 2h, office = surgery − 15min.
    arrival_24 = arrival_time_str(surgery.scheduled_start_time,
                                    surgery.selected_facility)
    arrival_time = ""
    if arrival_24:
        # Accept HH:MM and HH:MM:SS — earlier code unpacked exactly 2,
        # which raised ValueError on the seconds-bearing form. Cap at
        # the first two parts. (Fable portal audit M5.)
        parts = arrival_24.split(":")
        if len(parts) >= 2:
            try:
                hh, mm = int(parts[0]), int(parts[1])
                h12 = ((hh - 1) % 12) + 1
                arrival_time = f"{h12}:{mm:02d} {'AM' if hh < 12 else 'PM'}"
            except ValueError:
                arrival_time = ""

    ctx = {
        "patient_name":   first,
        "surgery_date":   surgery_date,
        "surgery_time":   surgery_time,
        "surgery_when":   surgery_when,
        "arrival_time":   arrival_time,
        "facility_name":  facility_name,
        "practice_phone": os.environ.get("WWC_PRACTICE_PHONE", "").strip(),
    }
    ctx.update(extras)
    return ctx


def _segments(text: str) -> int:
    """Twilio segment count.

    GSM-7 encoding: 160 chars per single-segment SMS, 153 chars per
    continuation segment (UDH overhead).
    Unicode / UCS-2 (any non-GSM-7 char, including smart quotes and
    accented letters that show up in patient names): 70 single, 67
    continuation. (Fable portal audit M3.)
    """
    if _is_gsm7_only(text):
        return 1 if len(text) <= 160 else (len(text) + 152) // 153
    # UCS-2 — any non-GSM-7 char forces the entire body to UCS-2
    return 1 if len(text) <= 70 else (len(text) + 66) // 67


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
    consent_override: bool = False,
) -> PatientSms:
    """Send a patient SMS and write the audit row.

    Modes:
      Template send  — pass `kind`. Body comes from SmsTemplate and is
                       rendered with `context`.
      Ad-hoc send    — pass `kind=None` + `ad_hoc_body`.

    The `surgery` argument is required for template sends (to look up
    consent + cell_phone). For ad-hoc to an unknown patient (no Surgery
    row), pass `consent_override=True` along with `to_phone` +
    `chart_number` — the override is recorded in `failure_reason` for
    audit. Without that override, surgery=None now raises rather than
    silently skipping the consent check. (Fable portal audit C2-sms.)
    """
    # Resolve recipient
    phone = to_phone or (surgery.cell_phone if surgery else None)
    phone = (phone or "").strip()

    # Consent gate — must be evaluated regardless of whether the caller
    # passed a Surgery row.
    if surgery is not None:
        if not surgery.sms_consent:
            return _record(db,
                surgery_id=surgery.id,
                chart_number=chart_number or surgery.chart_number,
                to_phone=phone or "(missing)",
                kind=kind, body="(unsent — no consent)",
                status="skipped",
                failure_reason="patient has not opted in to SMS",
                sent_by=sent_by, context=context, segments=None, twilio_sid=None)
    else:
        # No Surgery row — the consent check has to be made explicit.
        # If a related Surgery exists for this chart_number, check it;
        # otherwise refuse unless the caller passes consent_override.
        related = None
        if chart_number:
            related = (db.query(Surgery)
                         .filter(Surgery.chart_number == chart_number)
                         .order_by(Surgery.created_at.desc())
                         .first())
        if related is not None and not related.sms_consent:
            return _record(db,
                surgery_id=related.id,
                chart_number=chart_number,
                to_phone=phone or "(missing)",
                kind=kind, body="(unsent — no consent)",
                status="skipped",
                failure_reason="patient has not opted in to SMS",
                sent_by=sent_by, context=context, segments=None, twilio_sid=None)
        if related is None and not consent_override:
            return _record(db,
                surgery_id=None,
                chart_number=chart_number,
                to_phone=phone or "(missing)",
                kind=kind, body="(unsent — no consent record)",
                status="skipped",
                failure_reason=("no Surgery row to check consent and "
                                "consent_override=False — refusing to send"),
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

    # Rate-limit guards. Cheap defense against a frontend loop or
    # mistakenly batched script: at most N sends to one phone per hour,
    # and at most M sends total per hour. Both query the audit table we
    # were going to write to anyway. (Fable recalls audit M2.)
    from datetime import timedelta as _td
    _PER_NUMBER_HOURLY = int(os.environ.get("PATIENT_SMS_PER_NUMBER_HOURLY", "5"))
    _GLOBAL_HOURLY = int(os.environ.get("PATIENT_SMS_GLOBAL_HOURLY", "200"))
    one_hour_ago = now_utc_naive() - _td(hours=1)
    per_num = (db.query(PatientSms)
                  .filter(PatientSms.to_phone == phone,
                          PatientSms.status == "sent",
                          PatientSms.created_at >= one_hour_ago)
                  .count())
    if per_num >= _PER_NUMBER_HOURLY:
        return _record(db,
            surgery_id=(surgery.id if surgery else None),
            chart_number=chart_number or (surgery.chart_number if surgery else None),
            to_phone=phone, kind=kind, body=body,
            status="skipped",
            failure_reason=(f"per-number rate limit ({_PER_NUMBER_HOURLY}/hr) "
                            "reached"),
            sent_by=sent_by, context=context, segments=None, twilio_sid=None)
    glob = (db.query(PatientSms)
              .filter(PatientSms.status == "sent",
                      PatientSms.created_at >= one_hour_ago)
              .count())
    if glob >= _GLOBAL_HOURLY:
        return _record(db,
            surgery_id=(surgery.id if surgery else None),
            chart_number=chart_number or (surgery.chart_number if surgery else None),
            to_phone=phone, kind=kind, body=body,
            status="skipped",
            failure_reason=(f"global rate limit ({_GLOBAL_HOURLY}/hr) reached"),
            sent_by=sent_by, context=context, segments=None, twilio_sid=None)

    segments = _segments(body)
    try:
        sid = send_sms(phone, body)
    except Exception as e:
        log.warning("patient sms Twilio raised: %s", e)
        sid = None
    status = "sent" if sid is not None else "failed"
    # Audit a successful send. If consent_override was used to bypass
    # the no-Surgery-row consent check, stamp that in failure_reason so
    # compliance can later demonstrate why a no-consent-record patient
    # was texted. (Fable recalls audit H5.)
    if sid is not None:
        if consent_override and surgery is None:
            failure_reason = ("sent with consent_override — no Surgery "
                              "consent record at the time of send")
        else:
            failure_reason = None
    else:
        failure_reason = "Twilio send returned None (check TWILIO_* config)"

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
