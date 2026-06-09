"""Auto-fax a completed LARC enrollment envelope to the dispensing pharmacy.

Called from the BoldSign webhook handler when an envelope reaches
Completed. Fetches the signed PDF from BoldSign, writes it to a temp
file, submits a RingCentral fax to the LarcPharmacy.fax number, and
records the resulting message id on the LarcEnrollmentEnvelope row.

Idempotent: skips faxes that already have a successful `faxed_at` /
non-failed `fax_status`. Manual retries go through `fax_envelope(...,
force=True)`.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta
from typing import Optional


# Auto-retry backoff schedule. Index = the attempt number we *just*
# completed (1 = the original webhook attempt, 2..6 = retry attempts).
# The value is the delay before the NEXT attempt. After attempt 6 the
# row is marked terminally failed and a notification email goes out.
# Chosen to converge fast on transient RingCentral / network blips
# while still covering an overnight outage with the long tail.
_FAX_RETRY_BACKOFF = [
    timedelta(minutes=5),     # after attempt 1 (the original)
    timedelta(minutes=30),    # after attempt 2
    timedelta(hours=2),       # after attempt 3
    timedelta(hours=6),       # after attempt 4
    timedelta(hours=24),      # after attempt 5 (the last retry slot)
]
_FAX_MAX_ATTEMPTS = len(_FAX_RETRY_BACKOFF) + 1   # = 6

import httpx
from sqlalchemy.orm import Session, object_session

from app.models.larc import LarcAssignment, LarcEnrollmentEnvelope, LarcPharmacy
from app.services.audit_service import log_action
from app.services.fax_service import send_fax
from app.services.practice_settings import get_all as get_all_practice_settings

log = logging.getLogger(__name__)

API_BASE = "https://api.boldsign.com"


class LarcFaxError(Exception):
    pass


def _api_key() -> str:
    return os.environ.get("BOLDSIGN_API_KEY", "").strip()


def _http() -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE, timeout=30.0,
        headers={"X-API-KEY": _api_key(), "Accept": "application/pdf"},
    )


# ─── Fetch signed PDF ──────────────────────────────────────────────

def fetch_signed_pdf(envelope_id: str) -> bytes:
    """Download the finished BoldSign PDF as bytes. Raises LarcFaxError
    on non-2xx — caller decides whether to retry or record failure."""
    if not _api_key():
        raise LarcFaxError("BoldSign API key not configured")
    with _http() as c:
        r = c.get("/v1/document/download",
                  params={"documentId": envelope_id})
    if r.status_code >= 300:
        raise LarcFaxError(
            f"BoldSign download failed: {r.status_code} {r.text[:200]}"
        )
    return r.content


# ─── Cover-sheet wording ──────────────────────────────────────────

def _cover_text(env: LarcEnrollmentEnvelope, a: LarcAssignment,
                settings: dict) -> str:
    device_name = (a.device.device_type.name
                    if a.device and a.device.device_type else "LARC device")
    practice = settings.get("practice_name") or "WWC Gynecology"
    phone    = settings.get("practice_contact_phone") or ""
    parts = [
        f"Patient: {a.patient_name or ''}",
        f"Chart: {a.chart_number or ''}",
        f"Device: {device_name} pharmacy enrollment",
        f"From: {practice}" + (f", {phone}" if phone else ""),
    ]
    return "\n".join(p for p in parts if p)


# ─── Public: fax (or re-fax) one envelope ─────────────────────────

def fax_envelope(db: Session, env: LarcEnrollmentEnvelope,
                  *, by_email: str = "system:webhook",
                  force: bool = False) -> dict:
    """Fetch the signed PDF and fax it to LarcAssignment.pharmacy.fax.

    Returns {ok, message_id, status, fax_to} on success or
    {ok: False, error} on failure (also persists the failure on the row).

    Skips work if a prior attempt already succeeded (`faxed_at` set with
    non-failed `fax_status`) unless `force=True`.
    """
    if env.faxed_at and env.fax_status not in ("SendingFailed", "fax_failed", None) \
            and not force:
        return {"ok": True, "skipped": True, "reason": "already faxed",
                "message_id": env.fax_message_id, "fax_to": env.fax_to}

    a: LarcAssignment = env.assignment or (
        db.query(LarcAssignment)
          .filter(LarcAssignment.id == env.assignment_id)
          .first()
    )
    if a is None:
        return _record_failure(db, env, by_email,
                                "Assignment missing — can't determine pharmacy")

    pharm: Optional[LarcPharmacy] = None
    if a.pharmacy_id:
        pharm = (db.query(LarcPharmacy)
                   .filter(LarcPharmacy.id == a.pharmacy_id).first())
    if pharm is None or not (pharm.fax or "").strip():
        return _record_failure(db, env, by_email,
                                "No pharmacy fax number set on the assignment")

    # Pull the signed PDF
    try:
        pdf = fetch_signed_pdf(env.boldsign_envelope_id)
    except LarcFaxError as exc:
        return _record_failure(db, env, by_email, str(exc))

    settings = get_all_practice_settings(db)
    cover = _cover_text(env, a, settings)

    # Write PDF to a temp file so send_fax can stream it
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"larc-enroll-{env.boldsign_envelope_id[:8]}-",
        suffix=".pdf", delete=False,
    )
    try:
        tmp.write(pdf); tmp.flush(); tmp.close()
        env.fax_attempts = (env.fax_attempts or 0) + 1
        env.fax_to = pharm.fax
        result = send_fax(
            to_number=pharm.fax,
            file_path=tmp.name,
            cover_page_text=cover,
            patient_name=a.patient_name,
        )
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass

    if not result.get("success"):
        return _record_failure(
            db, env, by_email,
            result.get("error") or result.get("detail") or "Unknown fax error",
        )

    env.fax_message_id = result.get("message_id")
    env.fax_status    = result.get("status") or "Queued"
    env.faxed_at      = datetime.utcnow()
    env.last_fax_error = None
    # Clear retry state — a successful send (whether the original or a
    # later sweep attempt) ends the retry queue for this envelope.
    env.next_fax_retry_at = None
    env.fax_terminally_failed_at = None
    # Reflect the successful send in env.status — previously only flipped
    # signed→faxed, which left the row inconsistent when a fax_failed or
    # pending row was successfully retried (faxed_at set, but status
    # stuck on the old value). Skip terminal envelope statuses where a
    # status flip would lose audit fidelity.
    if env.status not in ("declined", "voided", "revoked", "expired"):
        env.status = "faxed"

    # Bump the assignment SLA clock — same milestone the manual /fax-pharmacy
    # endpoint sets when staff faxed by hand.
    if not a.request_faxed_at:
        a.request_faxed_at = datetime.utcnow()

    log_action(
        db, "LARC_ENROLLMENT_FAXED", "larc_assignment",
        resource_id=str(a.id),
        patient_id=a.chart_number,
        user_name=by_email,
        description=(
            f"Faxed enrollment PDF to {pharm.name} ({pharm.fax}) — "
            f"RingCentral message {env.fax_message_id}"
        ),
        new_values={
            "boldsign_envelope_id": env.boldsign_envelope_id,
            "fax_to": pharm.fax,
            "fax_message_id": env.fax_message_id,
            "fax_status": env.fax_status,
            "attempts": env.fax_attempts,
        },
    )
    db.commit()
    return {"ok": True, "message_id": env.fax_message_id,
            "status": env.fax_status, "fax_to": pharm.fax}


def _record_failure(db: Session, env: LarcEnrollmentEnvelope,
                     by_email: str, msg: str) -> dict:
    """Mark the fax attempt as failed + audit. Schedules the next retry
    or marks the envelope terminally failed and notifies. Returns the
    failure dict the caller returns to its own caller."""
    env.fax_attempts = (env.fax_attempts or 0) + 1
    env.fax_status = "fax_failed"
    env.last_fax_error = msg
    # Mark envelope-level status as fax_failed when the row isn't already
    # in a terminal state (declined/voided/revoked/expired) and hasn't
    # been successfully faxed before. A failed retry of a previously
    # successful send must not erase the prior 'faxed' state.
    if env.status not in ("declined", "voided", "revoked", "expired", "faxed"):
        env.status = "fax_failed"

    # Schedule the next retry (or give up). The sweep in larc_sweeps
    # picks rows where next_fax_retry_at <= now() and runs them through
    # fax_envelope(force=True).
    now = datetime.utcnow()
    if env.fax_attempts < _FAX_MAX_ATTEMPTS:
        backoff = _FAX_RETRY_BACKOFF[env.fax_attempts - 1]
        env.next_fax_retry_at = now + backoff
        env.fax_terminally_failed_at = None
        terminal = False
    else:
        env.next_fax_retry_at = None
        env.fax_terminally_failed_at = now
        terminal = True

    log_action(
        db, ("LARC_ENROLLMENT_FAX_TERMINAL" if terminal
             else "LARC_ENROLLMENT_FAX_FAILED"),
        "larc_assignment",
        resource_id=str(env.assignment_id),
        user_name=by_email,
        description=(
            f"LARC enrollment fax permanently failed after {env.fax_attempts} "
            f"attempts: {msg}" if terminal
            else f"LARC enrollment fax failed (attempt {env.fax_attempts}/"
                 f"{_FAX_MAX_ATTEMPTS}): {msg}"),
        new_values={"error": msg, "attempts": env.fax_attempts,
                    "next_retry_at": (env.next_fax_retry_at.isoformat()
                                       if env.next_fax_retry_at else None),
                    "terminal": terminal},
        status="error",
        error_detail=msg,
    )
    db.commit()

    if terminal:
        try:
            _notify_terminal_fax_failure(db, env, msg)
        except Exception:
            log.exception("Failed to send LARC fax terminal-failure notification")

    return {"ok": False, "error": msg, "attempts": env.fax_attempts,
            "terminal": terminal}


def _notify_terminal_fax_failure(db: Session,
                                   env: LarcEnrollmentEnvelope, msg: str) -> None:
    """Email the practice when an envelope has run out of retry attempts.
    Address is configurable via LARC_FAX_FAILURE_NOTIFY_EMAIL (defaults
    to info@waldorfwomenscare.com)."""
    to_addr = os.environ.get("LARC_FAX_FAILURE_NOTIFY_EMAIL",
                              "info@waldorfwomenscare.com").strip()
    if not to_addr:
        return
    a: Optional[LarcAssignment] = env.assignment or (
        db.query(LarcAssignment).filter(LarcAssignment.id == env.assignment_id)
          .first())
    pharm = None
    if a and a.pharmacy_id:
        pharm = (db.query(LarcPharmacy)
                   .filter(LarcPharmacy.id == a.pharmacy_id).first())
    patient_name = (a.patient_name if a else "<unknown>")
    pharmacy_name = (pharm.name if pharm else "<unknown>")
    pharmacy_fax = (pharm.fax if pharm else "<unknown>")
    subject = f"[LARC] Pharmacy fax permanently failed — {patient_name}"
    text = (
        f"The LARC enrollment fax for {patient_name} could not be delivered to "
        f"{pharmacy_name} ({pharmacy_fax}) after {env.fax_attempts} attempts.\n\n"
        f"Last error: {msg}\n\n"
        f"BoldSign envelope: {env.boldsign_envelope_id}\n"
        f"Assignment: {env.assignment_id}\n\n"
        f"Please re-fax manually from the LARC dashboard "
        f"(Assignment -> Envelope -> Re-fax).\n"
    )
    html = (
        f"<p>The LARC enrollment fax for <strong>{patient_name}</strong> could "
        f"not be delivered to <strong>{pharmacy_name}</strong> "
        f"({pharmacy_fax}) after {env.fax_attempts} attempts.</p>"
        f"<p><strong>Last error:</strong> {msg}</p>"
        f"<p>BoldSign envelope: <code>{env.boldsign_envelope_id}</code><br/>"
        f"Assignment: <code>{env.assignment_id}</code></p>"
        f"<p>Please re-fax manually from the LARC dashboard.</p>"
    )
    from app.services.checklist_notifications import send_email
    send_email(to=to_addr, subject=subject, html_body=html, text_body=text)
