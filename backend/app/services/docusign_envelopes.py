"""DocuSign envelope-sending service (multi-envelope edition).

Sends one envelope per matched ConsentTemplate for a Surgery. Persists
each envelope as a SurgeryConsentEnvelope row. Surgery.consent_status
flips to 'signed' only when *every* envelope row reaches status='signed'.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.surgery import (
    ConsentTemplate, Surgery, SurgeryConsentEnvelope, SurgeryMilestone,
)
from app.services.consent_template_matcher import (
    TemplateMatch, match_templates_for_surgery, unmatched_procedures,
)
from app.services.docusign_client import auth_headers, envelopes_base_url


class DocuSignEnvelopeError(Exception):
    pass


# ── role builders ───────────────────────────────────────────────────

def _patient_role(s: Surgery) -> dict:
    if not s.email:
        raise DocuSignEnvelopeError(
            f"Surgery {s.id}: patient has no email on file — required for DocuSign."
        )
    return {
        "roleName": "Patient",
        "name": s.patient_name or "Patient",
        "email": s.email,
        "routingOrder": "1",
    }


def _provider_role() -> dict:
    if not settings.docusign_provider_email:
        raise DocuSignEnvelopeError(
            "DOCUSIGN_PROVIDER_NAME / DOCUSIGN_PROVIDER_EMAIL not configured."
        )
    return {
        "roleName": "Provider",
        "name": settings.docusign_provider_name,
        "email": settings.docusign_provider_email,
        "routingOrder": "2",
    }


def _witness_role() -> dict:
    if not settings.docusign_witness_email:
        raise DocuSignEnvelopeError(
            "DOCUSIGN_WITNESS_NAME / DOCUSIGN_WITNESS_EMAIL not configured."
        )
    return {
        "roleName": "Witness",
        "name": settings.docusign_witness_name,
        "email": settings.docusign_witness_email,
        "routingOrder": "3",
    }


# ── envelope create ─────────────────────────────────────────────────

def _create_envelope(s: Surgery, template: ConsentTemplate) -> str:
    body = {
        "templateId": template.docusign_template_id,
        "templateRoles": [
            _patient_role(s),
            _provider_role(),
            _witness_role(),
        ],
        "status": "sent",
        "emailSubject": f"WWC — {template.name} — {s.patient_name or 'Patient'}",
        "emailBlurb": (
            f"Please review and electronically sign the {template.name} form "
            f"for your upcoming procedure at Waldorf Women's Care."
        ),
        "customFields": {
            "textCustomFields": [
                {"name": "wwc_surgery_id", "value": s.id, "show": "false", "required": "false"},
                {"name": "wwc_template_name", "value": template.name, "show": "false", "required": "false"},
            ]
        },
    }
    url = f"{envelopes_base_url()}/envelopes"
    with httpx.Client(timeout=60) as client:
        r = client.post(url, headers=auth_headers(), json=body)
    if r.status_code not in (200, 201):
        raise DocuSignEnvelopeError(
            f"Envelope create failed for template {template.name!r}: "
            f"{r.status_code} {r.text}"
        )
    payload = r.json()
    envelope_id = payload.get("envelopeId")
    if not envelope_id:
        raise DocuSignEnvelopeError(f"No envelopeId in DocuSign response: {payload!r}")
    return envelope_id


# ── public API ──────────────────────────────────────────────────────

def send_consent_envelopes(db: Session, s: Surgery, *,
                            sent_by: str = "system",
                            ignore_warnings: bool = False) -> dict:
    """Send all matched envelopes for a surgery.

    Returns: {
      "sent": [{template_name, envelope_id, warning}, ...],
      "skipped": [{template_name, reason}, ...],
      "unmatched_procedures": [...],
      "warnings": [str, ...],
    }
    Raises DocuSignEnvelopeError if no templates match (unless surgery
    procedures list is empty) or on DocuSign API errors.
    """
    matches = match_templates_for_surgery(db, s)
    unmatched = unmatched_procedures(db, s)

    if not matches:
        raise DocuSignEnvelopeError(
            f"No consent templates match this surgery. "
            f"Unmatched procedures: {unmatched!r}. "
            f"Register a template in Settings → Consent Templates."
        )

    blocking_warnings = [m.warning for m in matches if m.warning and not ignore_warnings]
    if blocking_warnings:
        raise DocuSignEnvelopeError(
            "Send blocked by warnings: " + " | ".join(blocking_warnings)
            + "  Set ignore_warnings=true to send anyway."
        )

    # Index existing envelope rows by template id (to avoid duplicate sends
    # if the user clicks the button twice)
    existing_by_template = {
        e.template_id: e for e in s.consent_envelopes
    }

    sent: list[dict] = []
    skipped: list[dict] = []
    now = datetime.utcnow()

    for match in matches:
        prior = existing_by_template.get(match.template.id)
        if prior and prior.docusign_envelope_id and prior.status not in ("voided", "declined", "failed"):
            skipped.append({
                "template_id": str(match.template.id),
                "template_name": match.template.name,
                "envelope_id": prior.docusign_envelope_id,
                "reason": f"Already {prior.status}",
            })
            continue

        try:
            envelope_id = _create_envelope(s, match.template)
        except DocuSignEnvelopeError as e:
            # Persist a 'failed' row so the UI can surface the error and let staff retry
            row = prior or SurgeryConsentEnvelope(
                surgery_id=s.id,
                template_id=match.template.id,
            )
            row.status = "failed"
            row.last_error = str(e)
            row.last_synced_at = now
            if not prior:
                db.add(row)
            db.commit()
            raise

        row = prior or SurgeryConsentEnvelope(
            surgery_id=s.id,
            template_id=match.template.id,
        )
        row.docusign_envelope_id = envelope_id
        row.status = "sent"
        row.sent_at = now
        row.last_synced_at = now
        row.last_error = None
        if not prior:
            db.add(row)

        sent.append({
            "template_id": str(match.template.id),
            "template_name": match.template.name,
            "envelope_id": envelope_id,
            "warning": match.warning,
            "is_supplemental": match.is_supplemental,
        })

    # Surgery summary fields — keep consent_doc_id pointed at the FIRST
    # envelope for backwards compat with code that still reads it.
    if sent and not s.consent_doc_id:
        s.consent_doc_id = sent[0]["envelope_id"]
    if sent:
        s.consent_sent_at = s.consent_sent_at or now
        if s.consent_status not in ("signed",):
            s.consent_status = "sent"

    # Move the milestone to in_progress (any sent envelopes count)
    m = next((mm for mm in s.milestones if mm.kind == "consent"), None)
    if m and m.status not in ("done", "skipped") and (sent or skipped):
        m.status = "in_progress"
        m.started_at = m.started_at or now
        appended = "\nDocuSign envelopes sent: " + ", ".join(
            f"{x['template_name']} ({x['envelope_id'][:8]}…)" for x in sent
        ) if sent else ""
        m.notes = (m.notes or "") + appended

    db.commit()
    db.refresh(s)

    return {
        "sent": sent,
        "skipped": skipped,
        "unmatched_procedures": unmatched,
        "warnings": [m.warning for m in matches if m.warning],
    }


def _fetch_envelope_status(envelope_id: str) -> dict:
    url = f"{envelopes_base_url()}/envelopes/{envelope_id}"
    with httpx.Client(timeout=30) as client:
        r = client.get(url, headers=auth_headers())
    if r.status_code != 200:
        raise DocuSignEnvelopeError(
            f"Envelope fetch failed: {r.status_code} {r.text}"
        )
    return r.json()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _apply_status_to_row(row: SurgeryConsentEnvelope, env: dict) -> None:
    """Update a SurgeryConsentEnvelope row from a DocuSign envelope payload."""
    status = (env.get("status") or "").lower()
    row.status = status or row.status
    row.last_synced_at = datetime.utcnow()
    if status == "completed":
        row.status = "signed"
        row.signed_at = _parse_dt(env.get("completedDateTime")) or datetime.utcnow()
    elif status == "declined":
        row.declined_at = _parse_dt(env.get("declinedDateTime")) or datetime.utcnow()
    elif status == "voided":
        row.voided_at = _parse_dt(env.get("voidedDateTime")) or datetime.utcnow()


def reconcile_surgery_consent(db: Session, s: Surgery) -> None:
    """Recompute Surgery.consent_status / consent_signed_at from envelope rows.

    Rules:
      - All envelopes signed                  → consent_status='signed', stamp consent_signed_at
      - Any envelope declined or voided       → consent_status='sent' (still in flight; staff intervention needed)
      - Any envelope sent/delivered           → consent_status='sent'
      - No envelopes                          → leave alone
    """
    envs = list(s.consent_envelopes or [])
    if not envs:
        return
    if all(e.status == "signed" for e in envs):
        s.consent_status = "signed"
        latest = max((e.signed_at for e in envs if e.signed_at), default=None)
        s.consent_signed_at = latest or datetime.utcnow()
        m = next((mm for mm in s.milestones if mm.kind == "consent"), None)
        if m and m.status != "done":
            m.status = "done"
            m.completed_at = s.consent_signed_at
            m.completed_by = "docusign:reconcile"
        return
    if any(e.status in ("sent", "delivered", "signed") for e in envs):
        if s.consent_status != "signed":
            s.consent_status = "sent"


def sync_surgery_envelopes(db: Session, s: Surgery) -> dict:
    """Pull latest status for every envelope on this surgery and apply it.

    Returns a summary dict for the API caller.
    """
    out: list[dict] = []
    for row in list(s.consent_envelopes or []):
        if not row.docusign_envelope_id:
            continue
        try:
            env = _fetch_envelope_status(row.docusign_envelope_id)
        except DocuSignEnvelopeError as e:
            out.append({
                "envelope_id": row.docusign_envelope_id,
                "template_id": str(row.template_id),
                "error": str(e),
            })
            continue
        prior = row.status
        _apply_status_to_row(row, env)
        out.append({
            "envelope_id": row.docusign_envelope_id,
            "template_id": str(row.template_id),
            "previous_status": prior,
            "current_status": row.status,
        })
    reconcile_surgery_consent(db, s)
    db.commit()
    db.refresh(s)
    return {"envelopes": out, "consent_status": s.consent_status}


def void_envelope_row(db: Session, row: SurgeryConsentEnvelope,
                      reason: str = "Cancelled by practice") -> None:
    if not row.docusign_envelope_id:
        return
    url = f"{envelopes_base_url()}/envelopes/{row.docusign_envelope_id}"
    body = {"status": "voided", "voidedReason": reason}
    with httpx.Client(timeout=30) as client:
        r = client.put(url, headers=auth_headers(), json=body)
    if r.status_code not in (200, 201):
        raise DocuSignEnvelopeError(f"Void failed: {r.status_code} {r.text}")
    row.status = "voided"
    row.voided_at = datetime.utcnow()
    row.last_synced_at = datetime.utcnow()
    db.commit()


# ── legacy single-envelope shims (kept so old callers keep working) ─

def select_template_id(s: Surgery) -> Optional[str]:
    """Legacy. Returns the DocuSign template_id of the first matched
    primary template (first procedure). New code should call
    match_templates_for_surgery directly."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        matches = match_templates_for_surgery(db, s)
        for m in matches:
            if not m.is_supplemental:
                return m.template.docusign_template_id
        return None
    finally:
        db.close()


def send_consent_envelope(db: Session, s: Surgery, *, sent_by: str = "system") -> str:
    """Legacy single-envelope shim. Sends the FIRST matched envelope only.
    New code should call send_consent_envelopes (plural)."""
    result = send_consent_envelopes(db, s, sent_by=sent_by)
    if not result["sent"]:
        raise DocuSignEnvelopeError("No envelopes were sent.")
    return result["sent"][0]["envelope_id"]


def get_envelope_status(envelope_id: str) -> dict:
    """Legacy. Use sync_surgery_envelopes for full surgery sync."""
    return _fetch_envelope_status(envelope_id)
