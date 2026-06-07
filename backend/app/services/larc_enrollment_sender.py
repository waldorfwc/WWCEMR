"""LARC pharmacy-enrollment envelope sender.

Sends a BoldSign envelope for a pharmacy-order LARC assignment with
three signer roles (Receptionist → Patient → Provider). Persists a
`LarcEnrollmentEnvelope` row, audits, and bumps
`assignment.enrollment_sent_at` so the dashboard shows the milestone.

Phase 2 supports Nexplanon only. The per-template field map lives in
NEXPLANON_FIELD_MAP at the bottom of this module — Phase 5 will add
PARAGARD_FIELD_MAP and BAYER_FIELD_MAP and pick the right one by
template id.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.larc import LarcAssignment, LarcEnrollmentEnvelope
from app.services.audit_service import log_action
from app.services.practice_settings import get_all as get_all_practice_settings

log = logging.getLogger(__name__)

API_BASE = "https://api.boldsign.com"

# Template IDs the practice signed up for. Nexplanon ships in Phase 2;
# the others are wired through the same code path once their field
# labels + per-template maps are in.
NEXPLANON_TEMPLATE_ID = "9af154d6-0bc7-43f6-bf94-175b7daf27e6"
PARAGARD_TEMPLATE_ID  = "9a8f78cc-5de0-4b61-a05b-fa2cadb98ae7"
BAYER_TEMPLATE_ID     = "2918da35-1fed-4e9b-ad9c-4103c5db8e85"

# Default receptionist signer. Configurable via env so a test deploy
# doesn't accidentally fire forms at the production shared inbox.
DEFAULT_RECEPTIONIST_EMAIL = "info@waldorfwomenscare.com"


# ─── Exceptions ────────────────────────────────────────────────────

class LarcEnrollmentError(Exception):
    pass


# ─── HTTP / config ────────────────────────────────────────────────

def _api_key() -> str:
    return os.environ.get("BOLDSIGN_API_KEY", "").strip()


def _is_configured() -> bool:
    return bool(_api_key())


def _http() -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE, timeout=20.0,
        headers={
            "X-API-KEY": _api_key(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )


def _receptionist_email() -> str:
    return (os.environ.get("CONSENT_LARC_RECEPTIONIST_EMAIL")
            or DEFAULT_RECEPTIONIST_EMAIL).strip()


def _fallback_provider_email() -> str:
    """Used when assignment.inserting_provider_email is blank."""
    return (os.environ.get("CONSENT_LARC_PROVIDER_EMAIL")
            or os.environ.get("CONSENT_PROVIDER_EMAIL")
            or os.environ.get("DOCUSIGN_PROVIDER_EMAIL")
            or "").strip()


def _fallback_provider_name() -> str:
    return (os.environ.get("CONSENT_LARC_PROVIDER_NAME")
            or os.environ.get("CONSENT_PROVIDER_NAME")
            or os.environ.get("DOCUSIGN_PROVIDER_NAME")
            or "Dr. Aryian Cooke").strip()


# ─── Patient-name parsing ──────────────────────────────────────────

def _split_name(full_name: Optional[str]) -> tuple[str, str, str]:
    """LarcAssignment.patient_name is stored 'Last, First [Middle]' per the
    Smartsheet shape. Returns (first, middle_initial, last). Best-effort —
    blank strings on edge cases."""
    raw = (full_name or "").strip()
    if not raw:
        return "", "", ""
    if "," in raw:
        last, _, rest = raw.partition(",")
        rest = rest.strip()
        last = last.strip()
        parts = rest.split()
        first = parts[0] if parts else ""
        middle = parts[1][0] if len(parts) >= 2 else ""
        return first, middle, last
    # No comma — assume "First [Middle] Last"
    parts = raw.split()
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], parts[1][0], parts[-1]


def _friendly_name(full_name: Optional[str]) -> str:
    first, _, last = _split_name(full_name)
    return f"{first} {last}".strip() or (full_name or "Patient")


# ─── Field map (Nexplanon, Phase 2) ────────────────────────────────

def _build_nexplanon_fields(
    a: LarcAssignment,
    settings: dict[str, Optional[str]],
    *,
    sent_by_email: str,
    dispense: bool,
    provider_contact_preference: bool,
    provider_name_for_form: str,
    provider_npi_for_form: str,
) -> dict[str, list[dict]]:
    """Return {role_name: [{id, value} ...]} for the Nexplanon template.

    The BoldSign payload requires per-role `existingFormFields` arrays
    (BoldSign silently drops fields whose id isn't on that role), so we
    group up front."""
    p_first, p_middle, p_last = _split_name(a.patient_name)
    p_dob = a.patient_dob.strftime("%m/%d/%Y") if a.patient_dob else ""
    today = date.today().strftime("%m/%d/%Y")

    receptionist = []

    def add(role_list: list, field_id: str, value: Optional[str]):
        if value is None or value == "":
            return
        role_list.append({"id": field_id, "value": str(value)})

    # ── Receptionist: practice + provider config ─────────────────────
    add(receptionist, "practice_name",          settings.get("practice_name"))
    add(receptionist, "practice_address",       settings.get("practice_address"))
    add(receptionist, "practice_city",          settings.get("practice_city"))
    add(receptionist, "practice_state",         settings.get("practice_state"))
    add(receptionist, "practice_zip",           settings.get("practice_zip"))
    add(receptionist, "practice_taxid",         settings.get("practice_taxid"))
    add(receptionist, "practice_medicaid_lic",  settings.get("practice_medicaid_lic"))
    add(receptionist, "practice_contact",       settings.get("practice_contact"))
    add(receptionist, "practice_contact_phone", settings.get("practice_contact_phone"))
    add(receptionist, "practice_fax",           settings.get("practice_fax"))
    add(receptionist, "practice_email",         settings.get("practice_email"))
    add(receptionist, "provider_first_name",    settings.get("provider_first_name"))
    add(receptionist, "provider_last_name",     settings.get("provider_last_name"))
    add(receptionist, "provider_npi",           provider_npi_for_form)
    add(receptionist, "provider_name",          provider_name_for_form)
    add(receptionist, "app_name",               settings.get("app_name"))
    add(receptionist, "app_npi",                settings.get("app_npi"))

    # ── Receptionist: per-assignment ────────────────────────────────
    add(receptionist, "patient_full_name",     _friendly_name(a.patient_name))
    add(receptionist, "patient_dob",           p_dob)
    add(receptionist, "sign_on_behalf_of_patient", sent_by_email)
    add(receptionist, "patient_last_name2",    p_last)
    add(receptionist, "patient_first_name2",   p_first)
    add(receptionist, "patient_dob2",          p_dob)
    add(receptionist, "patient_cell_phone",    a.patient_phone or "")
    add(receptionist, "app_date",              today)

    # Checkboxes — BoldSign accepts {"id": "...", "value": "true"/"false"}
    if dispense:
        receptionist.append({"id": "dispense", "value": "true"})
    if provider_contact_preference:
        receptionist.append({"id": "provider_contact_preference", "value": "true"})

    # ── Patient role: demographics + insurance (pre-fill, patient edits)
    patient = []
    add(patient, "patient_first_name",   p_first)
    add(patient, "patient_last_name",    p_last)
    add(patient, "patient_middle_initial", p_middle)
    add(patient, "patient_dob1",         p_dob)
    add(patient, "patient_dob",          p_dob)   # second-page duplicate
    add(patient, "patient_full_name",    _friendly_name(a.patient_name))
    add(patient, "patient_cell",         a.patient_phone or "")
    add(patient, "patient_email",        a.patient_email or "")
    # Insurance — we only have the plan name on the assignment row
    add(patient, "patient_insurance_plan",  a.primary_insurance or "")
    add(patient, "patient_insurance_plan2", a.primary_insurance or "")

    # ── Provider role: no textbox prefill (signatures + dates only) ──
    provider = []

    return {
        "Receptionist": receptionist,
        "Patient":       patient,
        "Provider":      provider,
    }


# ─── Public send ───────────────────────────────────────────────────

def _resolve_provider(a: LarcAssignment) -> tuple[str, str, str]:
    """Return (email, display_name, npi) for the Provider signer role."""
    settings_db = None
    email = (a.inserting_provider_email or "").strip()
    name  = (a.inserting_provider_name  or "").strip()
    npi   = (a.inserting_provider_npi   or "").strip()
    if not email:
        email = _fallback_provider_email()
    if not name:
        name = _fallback_provider_name()
    return email, name, npi


def send_enrollment_envelope(
    db: Session,
    assignment: LarcAssignment,
    *,
    sent_by_email: str,
    dispense: bool = False,
    provider_contact_preference: bool = False,
) -> LarcEnrollmentEnvelope:
    """Create + send a pharmacy-enrollment envelope. Returns the new
    LarcEnrollmentEnvelope row.

    Validates prerequisites up front (template wired, patient email,
    provider resolvable) — surfaces an actionable error instead of
    burning a BoldSign envelope on bad data."""
    if not _is_configured():
        raise LarcEnrollmentError("BoldSign API key not configured")

    # Prerequisites
    dt = assignment.device.device_type if assignment.device else None
    if not dt:
        raise LarcEnrollmentError("Assignment has no device_type — wire one up first.")
    template_id = dt.enrollment_form_template
    if not template_id:
        raise LarcEnrollmentError(
            f"No BoldSign template ID configured for device type {dt.name!r}."
        )
    if template_id != NEXPLANON_TEMPLATE_ID:
        # Phase 2 ships Nexplanon only. Paragard / Bayer light up in Phase 5.
        raise LarcEnrollmentError(
            f"Enrollment sender for {dt.name!r} not yet implemented "
            "(Phase 2 supports Nexplanon only)."
        )
    if not (assignment.patient_email or "").strip():
        raise LarcEnrollmentError(
            "Assignment is missing patient_email — fill it in before sending."
        )

    provider_email, provider_name, provider_npi = _resolve_provider(assignment)
    if not provider_email:
        raise LarcEnrollmentError(
            "No inserting provider email on the assignment AND no fallback "
            "configured (set CONSENT_PROVIDER_EMAIL env var)."
        )

    settings = get_all_practice_settings(db)
    # The form prints provider_npi on the signature line; prefer the
    # per-assignment override, fall back to practice settings.
    npi_for_form = provider_npi or (settings.get("provider_npi") or "")

    fields_by_role = _build_nexplanon_fields(
        assignment, settings,
        sent_by_email=sent_by_email,
        dispense=dispense,
        provider_contact_preference=provider_contact_preference,
        provider_name_for_form=provider_name,
        provider_npi_for_form=npi_for_form,
    )

    roles_payload = [
        {
            "signerName":  "WWC Reception",
            "signerEmail": _receptionist_email(),
            "signerType":  "Signer",
            "signerRole":  "Receptionist",
            "signerOrder": 1,
            "roleIndex":   1,
            "existingFormFields": fields_by_role["Receptionist"],
        },
        {
            "signerName":  _friendly_name(assignment.patient_name),
            "signerEmail": (assignment.patient_email or "").strip(),
            "signerType":  "Signer",
            "signerRole":  "Patient",
            "signerOrder": 2,
            "roleIndex":   2,
            "existingFormFields": fields_by_role["Patient"],
        },
        {
            "signerName":  provider_name,
            "signerEmail": provider_email,
            "signerType":  "Signer",
            "signerRole":  "Provider",
            "signerOrder": 3,
            "roleIndex":   3,
            "existingFormFields": fields_by_role["Provider"],
        },
    ]

    payload = {
        "title": f"WWC — {dt.name} Pharmacy Enrollment — {assignment.patient_name or 'Patient'}",
        "message": (
            f"Please review and electronically sign the {dt.name} pharmacy "
            f"enrollment form. Once all three signers complete, the form "
            f"will be faxed to the dispensing pharmacy."
        ),
        "roles": roles_payload,
        "enableSigningOrder": True,
    }

    # Send to BoldSign
    with _http() as c:
        r = c.post("/v1/template/send",
                    params={"templateId": template_id},
                    json=payload)
    if r.status_code >= 300:
        raise LarcEnrollmentError(
            f"BoldSign send failed: {r.status_code} {r.text[:300]}"
        )
    body = r.json() or {}
    doc_id = (body.get("documentId")
              or body.get("documentid")
              or body.get("DocumentId"))
    if not doc_id:
        raise LarcEnrollmentError(f"BoldSign response missing documentId: {body!r}")

    # Persist envelope row + bump assignment.enrollment_sent_at
    env = LarcEnrollmentEnvelope(
        assignment_id=assignment.id,
        boldsign_template_id=template_id,
        boldsign_envelope_id=doc_id,
        status="sent",
        sent_at=datetime.utcnow(),
        sent_by=sent_by_email,
    )
    db.add(env)
    if not assignment.enrollment_sent_at:
        assignment.enrollment_sent_at = datetime.utcnow()

    log_action(
        db, "LARC_ENROLLMENT_SENT", "larc_assignment",
        resource_id=str(assignment.id),
        patient_id=assignment.chart_number,
        user_name=sent_by_email,
        description=(
            f"Sent {dt.name} enrollment envelope for {assignment.patient_name} "
            f"(BoldSign id {doc_id})"
        ),
        new_values={
            "template_id": template_id,
            "boldsign_envelope_id": doc_id,
            "receptionist": _receptionist_email(),
            "patient": assignment.patient_email,
            "provider": provider_email,
        },
    )
    db.commit()
    db.refresh(env)
    return env
