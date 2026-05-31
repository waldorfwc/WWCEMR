"""Daily reminder sweep for upcoming surgeries.

For each lead-day in surgery_config.reminder_lead_days (default [3, 1]):
  find every active Surgery with scheduled_date == today + lead_days
  AND no PatientEmail with template_kind='surgery_reminder' already
  recorded for that (surgery_id, lead_days) pair.

For each match, send the `surgery_reminder` email via send_patient_email.
Soft-fail per row — one patient's bad email address doesn't block the
rest. Idempotent: a second run on the same day is a no-op.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.patient_email import PatientEmail
from app.models.surgery import Surgery
from app.models.surgery_config import SurgeryConfig
from app.services.patient_email import send_patient_email

log = logging.getLogger(__name__)


ACTIVE_STATUSES = ("new", "in_progress", "confirmed", "hold")


def _lead_days(db: Session) -> list[int]:
    row = (db.query(SurgeryConfig)
             .filter(SurgeryConfig.key == "reminder_lead_days")
             .first())
    if row and isinstance(row.value, list):
        return [int(d) for d in row.value]
    return [3, 1]


def _already_sent_for(db: Session, surgery_id, lead_days: int) -> bool:
    """A reminder for THIS lead_days bucket was already sent if any
    PatientEmail row exists for this surgery with template_kind=
    'surgery_reminder' and context.days_until == str(lead_days)."""
    rows = (db.query(PatientEmail)
              .filter(PatientEmail.surgery_id == surgery_id,
                       PatientEmail.template_kind == "surgery_reminder")
              .all())
    for r in rows:
        ctx = r.context or {}
        if str(ctx.get("days_until")) == str(lead_days):
            return True
    return False


def _sms_already_sent_for(db: Session, surgery_id, lead_days: int) -> bool:
    from app.models.patient_sms import PatientSms
    rows = (db.query(PatientSms)
              .filter(PatientSms.surgery_id == surgery_id,
                       PatientSms.template_kind == "sms_surgery_reminder")
              .all())
    for r in rows:
        ctx = r.context or {}
        if str(ctx.get("days_until")) == str(lead_days):
            return True
    return False


def run_reminder_sweep(db: Session, today: date | None = None) -> dict:
    """Returns a summary dict for logging/admin."""
    today = today or date.today()
    summary = {"today": today.isoformat(), "lead_days": [], "sent": 0, "skipped": 0}

    for lead in _lead_days(db):
        target = today + timedelta(days=lead)
        candidates = (db.query(Surgery)
                        .filter(Surgery.scheduled_date == target,
                                 Surgery.status.in_(ACTIVE_STATUSES))
                        .all())
        for s in candidates:
            if _already_sent_for(db, s.id, lead):
                summary["skipped"] += 1
                continue
            procedure = ""
            if s.procedures:
                procedure = s.procedures[0].get("name", "")
            slot_start = ""
            if s.slots:
                first = s.slots[0]
                if first and first.start_time:
                    slot_start = first.start_time.strftime("%H:%M")
            send_patient_email(
                db, kind="surgery_reminder",
                to_email=s.email,
                context={
                    "patient_name": s.patient_name,
                    "surgery_date": target.isoformat(),
                    "start_time":   slot_start,
                    "facility":     s.selected_facility or "",
                    "procedure":    procedure,
                    "days_until":   str(lead),
                },
                sent_by="system:reminder_cron",
                surgery_id=s.id,
                chart_number=s.chart_number,
            )
            if not _sms_already_sent_for(db, s.id, lead):
                from app.services.patient_sms import (
                    send_patient_sms, build_sms_context,
                )
                send_patient_sms(
                    db, kind="sms_surgery_reminder",
                    surgery=s,
                    context=build_sms_context(s, days_until=str(lead)),
                    sent_by="system:reminder_cron",
                )
            summary["sent"] += 1
        summary["lead_days"].append({"days": lead, "candidates": len(candidates)})
    log.info("surgery reminder sweep: %s", summary)
    return summary
