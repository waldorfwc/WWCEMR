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
from datetime import datetime
from typing import Optional

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
    if env.status == "signed":
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
    """Mark the fax attempt as failed + audit. Returns the failure dict
    the caller can return to its own caller."""
    env.fax_attempts = (env.fax_attempts or 0) + 1
    env.fax_status = "fax_failed"
    env.last_fax_error = msg
    if env.status == "signed":
        env.status = "fax_failed"
    log_action(
        db, "LARC_ENROLLMENT_FAX_FAILED", "larc_assignment",
        resource_id=str(env.assignment_id),
        user_name=by_email,
        description=f"LARC enrollment fax failed: {msg}",
        new_values={"error": msg, "attempts": env.fax_attempts},
        status="error",
        error_detail=msg,
    )
    db.commit()
    return {"ok": False, "error": msg, "attempts": env.fax_attempts}
