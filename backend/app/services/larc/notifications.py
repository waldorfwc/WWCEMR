"""Per-step patient notifications for a LARC assignment (email always; SMS only
if the patient opted in). Idempotent per (assignment, step)."""
from __future__ import annotations
import os
from sqlalchemy.orm import Session
from app.models.patient_email import PatientEmail
from app.services.patient_email import send_patient_email
from app.services.patient_sms import send_patient_sms

STEP_KIND = {
    "responsibility_determined": "larc_responsibility_due",
    "responsibility_satisfied":  "larc_payment_receipt",
    "device_allocated":          "larc_device_allocated",
    "enrollment_completed":      "larc_enrollment_ready",
    "enrollment_faxed":          "larc_enrollment_faxed",
    "device_received":           "larc_device_received",
    "patient_notified":          "larc_ready",
}


def _portal_url() -> str:
    base = (os.environ.get("APP_BASE_URL") or "https://gw.waldorfwomenscare.com").rstrip("/")
    return f"{base}/larc-portal/login"


def _already_sent(db: Session, assignment_id, kind: str) -> bool:
    return (db.query(PatientEmail)
              .filter(PatientEmail.larc_assignment_id == assignment_id,
                      PatientEmail.template_kind == kind)
              .first() is not None)


def notify_larc_step(db: Session, a, step: str, *, sent_by: str = "system") -> None:
    kind = STEP_KIND.get(step)
    if not kind or _already_sent(db, a.id, kind):
        return
    ctx = {
        "patient_name": (a.patient_first_name or (a.patient_name or "").split(",")[-1]).strip() or "there",
        "portal_url": _portal_url(),
        "amount": f"{a.patient_responsibility:.2f}" if a.patient_responsibility else "",
    }
    if a.patient_email:
        row = send_patient_email(db, kind=kind, to_email=a.patient_email, context=ctx,
                                 sent_by=sent_by, chart_number=a.chart_number)
        if row is not None:
            row.larc_assignment_id = a.id
            db.commit()
    if a.sms_consent and a.patient_cell:
        srow = send_patient_sms(db, kind=kind, surgery=None, context=ctx, sent_by=sent_by,
                                to_phone=a.patient_cell, chart_number=a.chart_number,
                                consent_override=True)
        if srow is not None:
            srow.larc_assignment_id = a.id
            db.commit()
