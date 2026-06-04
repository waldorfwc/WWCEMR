"""BoldSign envelope service.

Replaces the DocuSign service that previously lived in
docusign_envelopes.py. Public interface preserved — callers don't change.

Configuration (env, both required for live API calls):
  BOLDSIGN_API_KEY        — X-API-KEY header value
  BOLDSIGN_WEBHOOK_SECRET — HMAC-SHA256 key used to verify webhook signatures

If BOLDSIGN_API_KEY is missing, send/reconcile soft-fail with logging —
the rest of the app boots normally.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.surgery import (
    Surgery, ConsentTemplate, SurgeryConsentEnvelope, SurgeryMilestone,
)
from app.services.consent_template_matcher import (
    TemplateMatch, match_templates_for_surgery, unmatched_procedures,
)
from app.services.patient_email import send_patient_email

log = logging.getLogger(__name__)

API_BASE = "https://api.boldsign.com"


class BoldSignEnvelopeError(Exception):
    pass


# ─── Configuration ──────────────────────────────────────────────────

def _api_key() -> str:
    return os.environ.get("BOLDSIGN_API_KEY", "").strip()


def _is_configured() -> bool:
    return bool(_api_key())


def _headers() -> dict:
    return {
        "X-API-KEY": _api_key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _http() -> httpx.Client:
    return httpx.Client(base_url=API_BASE, timeout=20.0, headers=_headers())


# ─── Template selection ─────────────────────────────────────────────

def select_template_id(s: Surgery, db: Optional[Session] = None) -> Optional[str]:
    """Pick the boldsign_template_id of the first matched primary ConsentTemplate.

    Mirrors the legacy DocuSign select_template_id: uses
    match_templates_for_surgery and returns the first non-supplemental match.
    Returns None if no match found.

    If `db` is not provided, opens its own session from SessionLocal.
    """
    if db is not None:
        matches = match_templates_for_surgery(db, s)
        for m in matches:
            if not m.is_supplemental:
                return m.template.boldsign_template_id
        return None

    from app.database import SessionLocal
    _db = SessionLocal()
    try:
        matches = match_templates_for_surgery(_db, s)
        for m in matches:
            if not m.is_supplemental:
                return m.template.boldsign_template_id
        return None
    finally:
        _db.close()


# ─── Send flow ──────────────────────────────────────────────────────

def _format_patient_name(s: Surgery) -> str:
    """Surgery rows store names as 'Last, First [Middle]' (Smartsheet shape).
    Consent forms want the friendly 'First Last' rendering."""
    name = (s.patient_name or "").strip()
    if "," in name:
        last, _, rest = name.partition(",")
        rest  = rest.strip()
        last  = last.strip()
        if rest and last:
            return f"{rest} {last}"
    return name or "Patient"


def _build_prefill_fields(s: Surgery) -> list[dict]:
    """Field values to push into the BoldSign template.

    Each consent template in BoldSign should give its form fields one of
    the supported IDs/labels (see the alias lists below). We send under
    every alias so the coordinator can name a field "Patient Name" or
    "patient_name" or "PatientName" and either will populate. BoldSign
    ignores IDs that don't exist on the template.
    """
    first_proc = (s.procedures or [{}])[0]
    procedure  = (first_proc.get("description")
                  or first_proc.get("name") or "").strip()
    values = {
        "patient_name":   _format_patient_name(s),
        "patient_dob":    s.dob.strftime("%m/%d/%Y") if s.dob else "",
        "surgeon_name":   (s.surgeon_primary or "").strip(),
        "surgery_date":   (s.scheduled_date.strftime("%m/%d/%Y")
                            if s.scheduled_date else ""),
        "procedure_name": procedure,
        "facility":       (s.selected_facility or "").strip(),
        "chart_number":   (s.chart_number or "").strip(),
    }
    aliases = {
        "patient_name":   ["patient_name", "patientname", "PatientName",
                            "Patient Name", "patient name", "name"],
        "patient_dob":    ["patient_dob", "patientdob", "PatientDOB",
                            "Patient DOB", "dob", "DOB",
                            "Date of Birth", "date_of_birth", "DateOfBirth",
                            "patient_date_of_birth"],
        "surgeon_name":   ["surgeon_name", "surgeonname", "SurgeonName",
                            "Surgeon Name", "surgeon", "Surgeon",
                            "doctor_name", "DoctorName", "Doctor Name",
                            "doctor", "physician", "Physician"],
        "surgery_date":   ["surgery_date", "surgerydate", "SurgeryDate",
                            "Surgery Date", "procedure_date", "ProcedureDate",
                            "Procedure Date", "date_of_surgery"],
        "procedure_name": ["procedure_name", "procedurename", "ProcedureName",
                            "Procedure Name", "procedure", "Procedure"],
        "facility":       ["facility", "Facility", "hospital", "Hospital",
                            "location", "Location"],
        "chart_number":   ["chart_number", "chartnumber", "ChartNumber",
                            "Chart Number", "chart", "Chart",
                            "mrn", "MRN"],
    }
    out: list[dict] = []
    for key, value in values.items():
        if not value:
            continue
        for alias in aliases.get(key, [key]):
            out.append({"id": alias, "value": value})
    return out


def _get_template_field_ids(template_id: str) -> set[str]:
    """Fetch the form field IDs and Data Sync Tags on a BoldSign template.

    BoldSign rejects send-from-template requests when the alias spam in
    existingFormFields exceeds the number of fields the template actually
    has. We introspect the template once per send and only push values for
    IDs (or DataSyncTags) that exist.

    Soft-fails to empty set on transport errors — caller treats that the
    same as "no fields configured" and skips prefill.
    """
    if not _is_configured() or not template_id:
        return set()
    try:
        with _http() as c:
            r = c.get("/v1/template/properties",
                       params={"templateId": template_id})
        if r.status_code >= 300:
            log.warning("BoldSign template properties %s: %s %s",
                        template_id, r.status_code, r.text[:200])
            return set()
        data = r.json() or {}
    except Exception as exc:
        log.warning("BoldSign template properties %s: %s", template_id, exc)
        return set()
    out: set[str] = set()
    # BoldSign returns the field list under different keys across api
    # versions — formFields on send-from-template properties, documentFields
    # on some account tiers. Walk both.
    candidates = (data.get("formFields")
                  or data.get("documentFields")
                  or data.get("fields") or [])
    for f in candidates:
        if not isinstance(f, dict):
            continue
        for k in ("id", "fieldId", "name", "dataSyncTag", "tag"):
            v = f.get(k) or f.get(k[0].upper() + k[1:])
            if v:
                out.add(str(v).strip())
    return out


def _build_signer_payload(s: Surgery, template: ConsentTemplate) -> list[dict]:
    """Build BoldSign roles list.

    Order: Patient (signerOrder=1), Provider (signerOrder=2),
    Witness optional (signerOrder=3).
    Field names follow BoldSign's send-from-template schema:
    signerName/signerEmail/signerOrder/roleIndex.

    The patient role also carries existingFormFields so name / DOB /
    surgeon / procedure / surgery date populate the consent automatically.
    BoldSign 400s when existingFormFields outnumbers the template's
    actual fields, so we introspect the template first and keep only the
    aliases that exist on it.
    """
    known_ids = _get_template_field_ids(template.boldsign_template_id)
    prefill = [f for f in _build_prefill_fields(s) if f["id"] in known_ids]
    log.info("BoldSign prefill for template %s: %d/%d fields applied",
             template.boldsign_template_id, len(prefill),
             len(known_ids))
    roles = [{
        "signerName": _format_patient_name(s),
        "signerEmail": s.email or "",
        "signerType": "Signer",
        "signerRole": "Patient",
        "signerOrder": 1,
        "roleIndex": 1,
        "existingFormFields": prefill,
    }]
    provider_email = os.environ.get("CONSENT_PROVIDER_EMAIL", "").strip()
    provider_name = os.environ.get("CONSENT_PROVIDER_NAME", "Dr. Aryian Cooke").strip()
    if provider_email:
        roles.append({
            "signerName": provider_name,
            "signerEmail": provider_email,
            "signerType": "Signer",
            "signerRole": "Provider",
            "signerOrder": 2,
            "roleIndex": 2,
            # Surgeon role gets the same prefill set so any provider-side
            # fields (e.g. surgeon_name read-only label) populate too.
            "existingFormFields": prefill,
        })
    witness_email = os.environ.get("CONSENT_WITNESS_EMAIL", "").strip()
    if witness_email:
        roles.append({
            "signerName": "Witness",
            "signerEmail": witness_email,
            "signerType": "Signer",
            "signerRole": "Witness",
            "signerOrder": 3,
            "roleIndex": 3,
        })
    return roles


def _create_envelope(s: Surgery, template: ConsentTemplate) -> str:
    """Call BoldSign send-from-template; return BoldSign documentId.

    BoldSign expects templateId as a query parameter, with title/message/
    roles/enableSigningOrder in the JSON body."""
    if not _is_configured():
        raise BoldSignEnvelopeError("BoldSign API key not configured")
    payload = {
        "title": (
            f"WWC — {template.name} — {s.patient_name or 'Patient'}"
        ),
        "message": (
            f"Please review and electronically sign the {template.name} form "
            f"for your upcoming procedure at Waldorf Women's Care."
        ),
        "roles": _build_signer_payload(s, template),
        "enableSigningOrder": False,
    }
    with _http() as c:
        r = c.post(
            "/v1/template/send",
            params={"templateId": template.boldsign_template_id},
            json=payload,
        )
    if r.status_code >= 300:
        raise BoldSignEnvelopeError(
            f"BoldSign send failed for template {template.name!r}: "
            f"{r.status_code} {r.text[:300]}"
        )
    body = r.json()
    doc_id = (
        body.get("documentId")
        or body.get("documentid")
        or body.get("DocumentId")
    )
    if not doc_id:
        raise BoldSignEnvelopeError(
            f"BoldSign response missing documentId: {body!r}"
        )
    return doc_id


def send_consent_envelopes(
    db: Session,
    s: Surgery,
    *,
    sent_by: str = "system",
    ignore_warnings: bool = False,
) -> dict:
    """Send all matched envelopes for a surgery via BoldSign.

    Returns: {
      "sent": [{template_name, envelope_id, warning, is_supplemental}, ...],
      "skipped": [{template_name, reason}, ...],
      "unmatched_procedures": [...],
      "warnings": [str, ...],
    }

    Raises BoldSignEnvelopeError if no templates match or on API errors.
    Public interface mirrors the DocuSign service so K3 can swap imports.
    """
    if not _is_configured():
        raise BoldSignEnvelopeError("BoldSign API key not configured")

    matches = match_templates_for_surgery(db, s)
    unmatched = unmatched_procedures(db, s)

    if not matches:
        raise BoldSignEnvelopeError(
            f"No consent templates match this surgery. "
            f"Unmatched procedures: {unmatched!r}. "
            f"Register a template in Settings → Consent Templates."
        )

    blocking_warnings = [m.warning for m in matches if m.warning and not ignore_warnings]
    if blocking_warnings:
        raise BoldSignEnvelopeError(
            "Send blocked by warnings: " + " | ".join(blocking_warnings)
            + "  Set ignore_warnings=true to send anyway."
        )

    # Index existing envelope rows by template id to avoid duplicate sends
    existing_by_template = {
        e.template_id: e for e in (s.consent_envelopes or [])
    }

    sent: list[dict] = []
    skipped: list[dict] = []
    now = datetime.utcnow()
    sent_rows: list[SurgeryConsentEnvelope] = []

    for match in matches:
        prior = existing_by_template.get(match.template.id)
        if prior and prior.boldsign_envelope_id and prior.status not in (
            "voided", "declined", "failed"
        ):
            skipped.append({
                "template_id": str(match.template.id),
                "template_name": match.template.name,
                "envelope_id": prior.boldsign_envelope_id,
                "reason": f"Already {prior.status}",
            })
            continue

        try:
            doc_id = _create_envelope(s, match.template)
        except BoldSignEnvelopeError as e:
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
        row.boldsign_envelope_id = doc_id
        row.status = "sent"
        row.sent_at = now
        row.last_synced_at = now
        row.last_error = None
        if not prior:
            db.add(row)

        sent.append({
            "template_id": str(match.template.id),
            "template_name": match.template.name,
            "envelope_id": doc_id,
            "warning": match.warning,
            "is_supplemental": match.is_supplemental,
        })
        sent_rows.append(row)

    # Surgery summary fields — keep consent_doc_id pointing at the FIRST
    # envelope for backwards compat with code that still reads it.
    if sent and not s.consent_doc_id:
        s.consent_doc_id = sent[0]["envelope_id"]
    if sent:
        s.consent_sent_at = s.consent_sent_at or now
        if s.consent_status not in ("signed",):
            s.consent_status = "sent"

    # Move the milestone to in_progress (any sent envelopes count)
    m = next((mm for mm in (s.milestones or []) if mm.kind == "consent"), None)
    if m and m.status not in ("done", "skipped") and (sent or skipped):
        m.status = "in_progress"
        m.started_at = m.started_at or now
        appended = "\nBoldSign envelopes sent: " + ", ".join(
            f"{x['template_name']} ({x['envelope_id'][:8]}…)" for x in sent
        ) if sent else ""
        m.notes = (m.notes or "") + appended

    db.commit()
    db.refresh(s)

    # ── patient heads-up email (sent once per envelope-send call when at
    #    least one envelope was newly dispatched to BoldSign) ──────────
    if sent:
        send_patient_email(
            db,
            kind="docusign_consent_sent",
            to_email=s.email,
            context={
                "patient_name": s.patient_name or "Patient",
                "surgery_date": (
                    s.scheduled_date.isoformat() if s.scheduled_date else ""
                ),
            },
            sent_by=sent_by,
            surgery_id=s.id,
            chart_number=s.chart_number,
        )

    return {
        "sent": sent,
        "skipped": skipped,
        "unmatched_procedures": unmatched,
        "warnings": [m.warning for m in matches if m.warning],
    }


def send_consent_envelope(db: Session, s: Surgery, *, sent_by: str = "system") -> str:
    """Single-template variant. Returns the envelope id of the first sent."""
    result = send_consent_envelopes(db, s, sent_by=sent_by)
    if not result["sent"]:
        raise BoldSignEnvelopeError("No envelopes were sent.")
    return result["sent"][0]["envelope_id"]


# ─── Status fetch + reconcile ───────────────────────────────────────

def get_envelope_status(envelope_id: str) -> dict:
    """Fetch status from BoldSign. Returns parsed dict."""
    if not _is_configured():
        raise BoldSignEnvelopeError("BoldSign API key not configured")
    with _http() as c:
        r = c.get("/v1/document/properties", params={"documentId": envelope_id})
    if r.status_code >= 300:
        raise BoldSignEnvelopeError(
            f"BoldSign status fetch failed: {r.status_code} {r.text[:300]}"
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
    """Apply BoldSign status payload to a SurgeryConsentEnvelope row.

    BoldSign status values: InProgress | Completed | Declined | Expired |
    Revoked. Maps to the same status column used by the DocuSign version.

    Also captures the patient's per-signer completion timestamp into
    patient_signed_at — that's what the portal uses to distinguish
    'awaiting your signature' from 'awaiting countersignature'.
    """
    raw_status = (env.get("status") or env.get("Status") or "").lower()
    mapping = {
        "inprogress": "sent",
        "completed": "signed",
        "declined": "declined",
        "expired": "expired",
        "revoked": "voided",
    }
    row.status = mapping.get(raw_status, raw_status or row.status)
    row.last_synced_at = datetime.utcnow()

    if raw_status == "completed":
        row.signed_at = (
            _parse_dt(env.get("completedDateTime") or env.get("completedAt"))
            or datetime.utcnow()
        )
    elif raw_status == "declined":
        row.declined_at = (
            _parse_dt(env.get("declinedDateTime") or env.get("declinedAt"))
            or datetime.utcnow()
        )
    elif raw_status == "revoked":
        row.voided_at = (
            _parse_dt(env.get("revokedDateTime") or env.get("revokedAt"))
            or datetime.utcnow()
        )

    # Per-signer: look for the patient role (signerRole == "Patient" is the
    # canonical match we send when creating the envelope). Fall back to the
    # surgery's email if BoldSign sent that field. signerDetails uses the
    # same status enum ("Completed" when done).
    signers = env.get("signerDetails") or env.get("SignerDetails") or []
    surgery_email = ((row.surgery.email or "").strip().lower()
                      if row.surgery else "")
    patient = None
    for s in signers:
        role = (s.get("signerRole") or s.get("SignerRole") or "").lower()
        email = (s.get("signerEmail") or s.get("SignerEmail") or "").strip().lower()
        if role == "patient" or (surgery_email and email == surgery_email):
            patient = s
            break
    if patient and (patient.get("status") or patient.get("Status") or "").lower() == "completed":
        if not row.patient_signed_at:
            row.patient_signed_at = (
                _parse_dt(patient.get("signedDateTime")
                            or patient.get("completedDateTime"))
                or datetime.utcnow()
            )


def reconcile_surgery_consent(db: Session, s: Surgery) -> None:
    """Recompute Surgery.consent_status / consent_signed_at from envelope rows.

    Rules (preserved from DocuSign service):
      - All envelopes signed                  → consent_status='signed', stamp consent_signed_at
      - Any envelope declined or voided       → consent_status='sent' (staff intervention needed)
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
        m = next(
            (mm for mm in (s.milestones or []) if mm.kind == "consent"), None
        )
        if m and m.status != "done":
            m.status = "done"
            m.completed_at = s.consent_signed_at
            m.completed_by = "boldsign:reconcile"
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
        if not row.boldsign_envelope_id:
            continue
        try:
            env = get_envelope_status(row.boldsign_envelope_id)
        except BoldSignEnvelopeError as e:
            out.append({
                "envelope_id": row.boldsign_envelope_id,
                "template_id": str(row.template_id),
                "error": str(e),
            })
            continue
        prior = row.status
        _apply_status_to_row(row, env)
        out.append({
            "envelope_id": row.boldsign_envelope_id,
            "template_id": str(row.template_id),
            "previous_status": prior,
            "current_status": row.status,
        })
    reconcile_surgery_consent(db, s)
    db.commit()
    db.refresh(s)
    return {"envelopes": out, "consent_status": s.consent_status}


def get_embedded_sign_link(envelope_id: str, signer_email: str) -> str:
    """Fetch a BoldSign embedded sign URL for a specific signer email on
    a document. Used by the patient portal — the calling endpoint MUST
    pass the patient's email (surgery.email) and never the surgeon's or
    witness's email.

    BoldSign embedded sign URLs are short-lived (~5 min per their docs),
    so callers should fetch on-demand when the patient clicks Sign now,
    not at page load.
    """
    if not _is_configured():
        raise BoldSignEnvelopeError("BoldSign API key not configured")
    with _http() as c:
        r = c.get(
            "/v1/document/getEmbeddedSignLink",
            params={"documentId": envelope_id, "signerEmail": signer_email},
        )
    if r.status_code >= 300:
        raise BoldSignEnvelopeError(
            f"BoldSign sign-link fetch failed: {r.status_code} {r.text[:200]}"
        )
    body = r.json()
    url = body.get("signLink") or body.get("SignLink") or body.get("signUrl")
    if not url:
        raise BoldSignEnvelopeError(
            f"BoldSign response missing signLink: {body!r}"
        )
    return url


def download_signed_pdf(envelope_id: str) -> bytes:
    """Fetch the signed PDF for an envelope from BoldSign. Returns raw
    bytes. Should only be called for envelopes with status=signed or
    completed; BoldSign returns 404/422 for unsigned documents."""
    if not _is_configured():
        raise BoldSignEnvelopeError("BoldSign API key not configured")
    with _http() as c:
        r = c.get(
            "/v1/document/download",
            params={"documentId": envelope_id},
        )
    if r.status_code >= 300:
        raise BoldSignEnvelopeError(
            f"BoldSign PDF download failed: {r.status_code} {r.text[:200]}"
        )
    return r.content


def void_envelope_row(
    db: Session,
    row: SurgeryConsentEnvelope,
    reason: str = "Cancelled by practice",
) -> None:
    """Revoke the BoldSign envelope and mark the row voided."""
    if not row.boldsign_envelope_id:
        return
    if not _is_configured():
        raise BoldSignEnvelopeError("BoldSign API key not configured")
    with _http() as c:
        r = c.post(
            "/v1/document/revoke",
            params={"documentId": row.boldsign_envelope_id},
            json={"message": reason},
        )
    if r.status_code >= 300:
        raise BoldSignEnvelopeError(
            f"BoldSign revoke failed: {r.status_code} {r.text[:300]}"
        )
    row.status = "voided"
    row.voided_at = datetime.utcnow()
    row.last_synced_at = datetime.utcnow()
    db.commit()
