"""Boarding-slip email — manual multi-recipient send + scheduled auto-send.

This is the data-layer + service for the boarding-slip email feature (T1).
The router/cron wiring lives in T2. Two facilities need a boarding slip
(medstar, crmc); recipient lists are configured per-facility in Surgery
Settings and read via cfg().
"""
from __future__ import annotations

import logging
import smtplib
from datetime import timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app.utils.dt import now_utc_naive
from app.models.surgery import Surgery, SurgeryFile, SurgerySlot
from app.services.surgery.settings import cfg
from app.services.surgery.boarding_slip import generate_for_surgery
from app.services.checklist_notifications import _smtp_settings
from app.services.audit_service import log_action, ACTOR_SYSTEM

log = logging.getLogger(__name__)

# Inactive surgery statuses the auto-sweep ignores.
_INACTIVE_STATUSES = ("cancelled", "completed")

_FACILITY_LABELS = {
    "medstar": "MedStar Southern Maryland Hospital Center",
    "crmc":    "University of Maryland Charles Regional Medical Center",
}

_RECIPIENT_KEYS = {
    "medstar": "boarding_slip_recipients_medstar",
    "crmc":    "boarding_slip_recipients_crmc",
}


def recipients_for(db: Session, facility) -> list[str]:
    """Per-facility configured recipient emails ([] for non-hospital
    facilities). Cleans (strip/lower, drop blanks + non-email entries)."""
    key = _RECIPIENT_KEYS.get(facility)
    if not key:
        return []
    raw = cfg(db, key) or []
    return [e.strip().lower() for e in raw if e and "@" in e]


def _latest_slip(db: Session, surgery_id) -> SurgeryFile | None:
    return (db.query(SurgeryFile)
              .filter(SurgeryFile.surgery_id == surgery_id,
                      SurgeryFile.kind == "boarding_slip")
              .order_by(SurgeryFile.uploaded_at.desc())
              .first())


def send_boarding_slip_email(db: Session, s: Surgery, file: SurgeryFile,
                             recipients: list[str], *, sent_by: str,
                             subject: str | None = None,
                             message: str | None = None) -> dict:
    """Email the boarding-slip PDF to all recipients in one message.

    Records send history on the file + a PHI_BOARDING_SLIP_SENT audit log.
    Returns {"ok": True, "to": [...]}. Raises ValueError on
    SMTP-not-configured or send failure (caller maps to HTTP)."""
    recips = [e.strip().lower() for e in (recipients or []) if e and "@" in e]
    if not recips:
        raise ValueError("no valid recipients")

    smtp_cfg = _smtp_settings()
    if not (smtp_cfg["host"] and smtp_cfg["from"]):
        raise ValueError("SMTP isn't configured on this server.")

    # Pull PDF bytes via the storage adapter (works on local + GCS).
    from app.services.storage import read_blob, is_legacy_local_path
    if is_legacy_local_path(file.path):
        raise ValueError("This file predates the cloud migration and is no "
                         "longer available.")
    try:
        pdf_bytes = read_blob(file.path)
    except FileNotFoundError:
        raise ValueError("Boarding slip file is missing.")

    facility_label = _FACILITY_LABELS.get(s.selected_facility or "",
                                           s.selected_facility or "")
    subj = (subject or
            f"Boarding slip — {s.patient_name or 'patient'} — {facility_label}")
    body_text = (
        message
        or f"Attached is the boarding slip for {s.patient_name or 'this patient'}"
           f" (chart #{s.chart_number or '—'}) at {facility_label}."
    )
    actor_local = (sent_by or "system").split("@")[0]
    body_html = (
        f"<p>{body_text}</p>"
        f"<p style='color:#888;font-size:11px'>"
        f"Sent from Waldorf Women's Care · {actor_local}</p>"
    )

    msg = MIMEMultipart()
    msg["Subject"] = subj
    msg["From"] = smtp_cfg["from"]
    msg["To"] = ", ".join(recips)
    msg.attach(MIMEText(body_html, "html"))
    attach = MIMEApplication(pdf_bytes, _subtype="pdf")
    attach.add_header("Content-Disposition", "attachment",
                      filename=file.filename or "boarding_slip.pdf")
    msg.attach(attach)

    def _record_send(status: str, error: str | None = None):
        hist = list(file.send_history or [])
        entry = {
            "at":     now_utc_naive().isoformat(),
            "by":     sent_by,
            "kind":   "email",
            "to":     recips,
            "status": status,
        }
        if error:
            entry["error"] = error
        hist.append(entry)
        file.send_history = hist
        from sqlalchemy.orm.attributes import flag_modified as _fm
        _fm(file, "send_history")

    try:
        with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"]) as smtp:
            smtp.starttls()
            if smtp_cfg["user"] and smtp_cfg["password"]:
                smtp.login(smtp_cfg["user"], smtp_cfg["password"])
            smtp.sendmail(smtp_cfg["from"], recips, msg.as_string())
    except Exception as exc:
        _record_send("failed", error=str(exc))
        db.commit()
        raise ValueError(f"Email send failed: {exc}")

    _record_send("sent")

    # HIPAA outbound audit: who sent a PHI document where.
    log_action(
        db,
        action="PHI_BOARDING_SLIP_SENT",
        resource_type="surgery",
        resource_id=str(s.id),
        patient_id=s.chart_number or None,
        actor=sent_by or ACTOR_SYSTEM,
        description=(f"Emailed boarding slip for surgery "
                     f"{s.surgery_number or s.id} to {', '.join(recips)}"),
        defer_commit=True,
    )
    db.commit()
    return {"ok": True, "to": recips}


def auto_email_sweep(db: Session, *, now=None) -> dict:
    """Cron body. Emails the boarding slip to per-facility recipients once a
    surgery date has been selected for >= the configured number of hours.

    Returns {"sent", "skipped_no_recipients", "errors"} or {"skipped": ...}.
    """
    if not cfg(db, "boarding_slip_auto_email_enabled"):
        return {"skipped": "disabled"}

    hours = int(cfg(db, "boarding_slip_auto_email_hours") or 0)
    now = now or now_utc_naive()
    cutoff = now - timedelta(hours=hours)

    # Active hospital surgeries not yet auto-emailed whose selected date
    # (= earliest slot booking) is at least `hours` old.
    rows = (db.query(Surgery, SurgerySlot.created_at)
              .join(SurgerySlot, SurgerySlot.surgery_id == Surgery.id)
              .filter(Surgery.selected_facility.in_(("medstar", "crmc")),
                      Surgery.boarding_slip_auto_emailed_at.is_(None),
                      ~Surgery.status.in_(_INACTIVE_STATUSES),
                      SurgerySlot.created_at <= cutoff)
              .all())

    # A surgery can have multiple slots; collapse to the earliest booking.
    earliest: dict = {}
    surgeries: dict = {}
    for s, slot_created in rows:
        if slot_created is None:
            continue
        if s.id not in earliest or slot_created < earliest[s.id]:
            earliest[s.id] = slot_created
        surgeries[s.id] = s

    sent = 0
    skipped_no_recipients = 0
    errors = 0
    for sid, s in surgeries.items():
        recips = recipients_for(db, s.selected_facility)
        if not recips:
            skipped_no_recipients += 1
            continue
        try:
            file = _latest_slip(db, s.id)
            if file is None:
                file = generate_for_surgery(db, s, by_email="system:auto")
            send_boarding_slip_email(db, s, file, recips, sent_by="system:auto")
            s.boarding_slip_auto_emailed_at = now
            sent += 1
        except Exception as exc:                       # pragma: no cover
            errors += 1
            log.warning("boarding-slip auto-email failed for surgery %s: %s",
                        sid, exc)
            continue

    db.commit()
    return {"sent": sent,
            "skipped_no_recipients": skipped_no_recipients,
            "errors": errors}
