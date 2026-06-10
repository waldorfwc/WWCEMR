"""LARC pharmacy-enrollment envelope sender + webhook applier.

Send side (`send_enrollment_envelope`): builds the BoldSign 3-signer
envelope (Receptionist → Patient → Provider), prefills from
PracticeConfig + the LARC assignment, persists a row, audits.

Webhook side (`apply_webhook_event`): called from the BoldSign webhook
when an envelope changes state. Updates per-signer timestamps + the
overall status, and triggers the auto-fax to the pharmacy on Completed.

Phase 2 supports Nexplanon only on the send path. Webhook handling is
template-agnostic — same status applier works for Paragard / Bayer
once their send paths land in Phase 5.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from app.utils.dt import now_utc_naive
from typing import Optional

import httpx
from sqlalchemy import or_
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
    # Prefer the explicit name fields when set, fall back to parsing
    # patient_name (legacy assignments + freeform input).
    parsed_f, parsed_m, parsed_l = _split_name(a.patient_name)
    p_first  = (a.patient_first_name     or parsed_f).strip()
    p_middle = (a.patient_middle_initial or parsed_m).strip()
    p_last   = (a.patient_last_name      or parsed_l).strip()
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
    # APP: per-assignment override beats practice-wide fallback. The
    # form has just two slots (app_name + app_npi); we don't split first/
    # last like the prescriber because the APP signature line is a single
    # printed name. _resolve_app returns the chosen values.
    app_name_for_form, app_npi_for_form = _resolve_app(a, settings)
    add(receptionist, "app_name",               app_name_for_form)
    add(receptionist, "app_npi",                app_npi_for_form)

    # ── Receptionist: per-assignment patient demographics + insurance ─
    # All demographic and insurance fields moved into the Receptionist
    # role on the template (Reception now fills the entire form; Patient
    # just signs). Set everything here so the form arrives ready for
    # signature with nothing missing.
    add(receptionist, "patient_first_name",       p_first)
    add(receptionist, "patient_last_name",        p_last)
    add(receptionist, "patient_middle_initial",   p_middle)
    add(receptionist, "patient_dob1",             p_dob)
    add(receptionist, "patient_dob",              p_dob)   # second-page dupe
    add(receptionist, "patient_full_name",        _friendly_name(a.patient_name))
    add(receptionist, "patient_address",          a.patient_address or "")
    add(receptionist, "patient_city",             a.patient_city or "")
    add(receptionist, "patient_state",            a.patient_state or "")
    add(receptionist, "patient_zip",              a.patient_zip or "")
    add(receptionist, "patient_cell",             (a.patient_cell or a.patient_phone) or "")
    add(receptionist, "patient_email",            a.patient_email or "")
    add(receptionist, "patient_insurance_plan",   a.primary_insurance or "")
    add(receptionist, "patient_insurance_plan2",  a.primary_insurance or "")
    add(receptionist, "patient_insurance_policy_no",   a.insurance_policy_no or "")
    add(receptionist, "patient_insurance_policy_no2",  a.insurance_policy_no or "")
    add(receptionist, "patient_insurance_group_no",    a.insurance_group_no or "")
    add(receptionist, "patient_insurance_group_no2",   a.insurance_group_no or "")
    # Per-assignment patient_*2 (second-page duplicates) + cell + dob2
    add(receptionist, "sign_on_behalf_of_patient", sent_by_email)
    add(receptionist, "patient_last_name2",       p_last)
    add(receptionist, "patient_first_name2",      p_first)
    add(receptionist, "patient_dob2",             p_dob)
    add(receptionist, "patient_cell_phone",       a.patient_phone or "")
    add(receptionist, "app_date",                 today)

    # Checkboxes — `dispense_no` is always-on per practice policy.
    receptionist.append({"id": "dispense_no", "value": "ON"})
    if provider_contact_preference:
        receptionist.append({"id": "provider_contact_preference", "value": "ON"})

    # ── Patient + Provider: signature-only roles (no prefill) ─────────
    # BoldSign rejects the send with "default value and read-only mode
    # should be same for same data synced fields" when the SAME field id
    # appears in multiple roles' existingFormFields arrays. Several
    # template fields (patient_dob, patient_full_name) are visible to
    # both Receptionist + Patient — Receptionist sets them, and BoldSign
    # auto-syncs to Patient via dataSyncTag. If a future template change
    # adds explicit Patient prefill here, dedup against recept_ids at the
    # assignment site (see _build_paragard_fields for the pattern).
    patient: list[dict] = []
    provider: list[dict] = []

    return {
        "Receptionist": receptionist,
        "Patient":       patient,
        "Provider":      provider,
    }


# ─── Field map (Paragard, Phase 5) ─────────────────────────────────

def _build_paragard_fields(
    a: LarcAssignment,
    settings: dict[str, Optional[str]],
    *,
    sent_by_email: str,
    dispense: bool,
    provider_contact_preference: bool,
    provider_name_for_form: str,
    provider_npi_for_form: str,
) -> dict[str, list[dict]]:
    """Paragard template — three roles now (Reception fills, Patient
    signs, Provider signs). Reception holds the full prefill payload
    so the form arrives signature-ready; Patient + Provider only sign."""
    p_dob = a.patient_dob.strftime("%m/%d/%Y") if a.patient_dob else ""

    def add(role_list: list, field_id: str, value: Optional[str]):
        if value is None or value == "":
            return
        role_list.append({"id": field_id, "value": str(value)})

    # ── Receptionist: full form prefill ───────────────────────────────
    app_name_for_form, app_npi_for_form = _resolve_app(a, settings)
    receptionist: list[dict] = []
    # Patient demographics
    add(receptionist, "patient_name",       _friendly_name(a.patient_name))
    add(receptionist, "patient_name2",      _friendly_name(a.patient_name))
    add(receptionist, "patient_dob",        p_dob)
    add(receptionist, "patient_address",    a.patient_address or "")
    add(receptionist, "patient_city",       a.patient_city or "")
    add(receptionist, "patient_state",      a.patient_state or "")
    add(receptionist, "patient_zip",        a.patient_zip or "")
    add(receptionist, "patient_phone_home", a.patient_phone or "")
    add(receptionist, "patient_cell",       (a.patient_cell or a.patient_phone) or "")
    # Insurance
    add(receptionist, "primary_insurance_name",     a.primary_insurance or "")
    add(receptionist, "primary_insurance_plan_no",  a.insurance_policy_no or "")
    add(receptionist, "primary_insurance_group_no", a.insurance_group_no or "")
    # Provider identity
    add(receptionist, "provider_name",        provider_name_for_form)
    add(receptionist, "provider_npi",         provider_npi_for_form)
    add(receptionist, "provider_lic",         settings.get("provider_lic"))
    add(receptionist, "provider_speciality",  settings.get("provider_speciality"))
    add(receptionist, "app_name",             app_name_for_form)
    # Practice identity
    add(receptionist, "practice_name",    settings.get("practice_name"))
    add(receptionist, "practice_address", settings.get("practice_address"))
    add(receptionist, "practice_city",    settings.get("practice_city"))
    add(receptionist, "practice_state",   settings.get("practice_state"))
    add(receptionist, "practice_zip",     settings.get("practice_zip"))
    # Ship-to address — leave blank by default; Reception writes a value
    # only when a different dock address is needed.
    add(receptionist, "practice_contact_name",  settings.get("practice_contact"))
    add(receptionist, "practice_contact_phone", settings.get("practice_contact_phone"))
    add(receptionist, "practice_contact_email", settings.get("practice_email"))
    add(receptionist, "practice_contact_fax",   settings.get("practice_fax"))
    # Pharmacy choice — tick the pharmacy-specific checkbox.
    # Paragard template has pharmacy_biologics (Biologics by McKesson)
    # and pharmacy_cityDrugs (City Drugs) at minimum.
    if a.pharmacy_id:
        from sqlalchemy.orm import object_session
        from app.models.larc import LarcPharmacy
        sess = object_session(a)
        if sess is not None:
            pharm = (sess.query(LarcPharmacy)
                          .filter(LarcPharmacy.id == a.pharmacy_id).first())
            pname = (pharm.name if pharm else "") or ""
            pharm_cb = None
            if "Biologics" in pname or "McKesson" in pname:
                pharm_cb = "pharmacy_biologics"
            elif "City Drugs" in pname:
                pharm_cb = "pharmacy_cityDrugs"
            if pharm_cb:
                receptionist.append({"id": pharm_cb, "value": "ON"})

    # ── Patient + Provider: signature-only roles ──────────────────────
    patient: list[dict] = []
    provider: list[dict] = []

    return {"Receptionist": receptionist, "Patient": patient, "Provider": provider}


# ─── Field map (Bayer Mirena/Skyla/Kyleena, Phase 5) ──────────────

def _build_bayer_fields(
    a: LarcAssignment,
    settings: dict[str, Optional[str]],
    *,
    sent_by_email: str,
    dispense: bool,
    provider_contact_preference: bool,
    provider_name_for_form: str,
    provider_npi_for_form: str,
) -> dict[str, list[dict]]:
    """Bayer (Mirena/Skyla/Kyleena) shared template — two roles:
    Receptionist (fills everything) + Provider (signs ONE of the three
    drug-specific signature lines). No Patient role on this template —
    Bayer's workflow has the practice fill on the patient's behalf."""
    p_first, p_middle, p_last = _split_name(a.patient_name)
    p_dob = a.patient_dob.strftime("%m/%d/%Y") if a.patient_dob else ""
    settings_last_first = ", ".join(
        x for x in [(settings.get("provider_last_name") or "").strip(),
                    (settings.get("provider_first_name") or "").strip()]
        if x
    )

    def add(role_list: list, field_id: str, value: Optional[str]):
        if value is None or value == "":
            return
        role_list.append({"id": field_id, "value": str(value)})

    # ── Receptionist: patient demographics + insurance + practice ────
    receptionist: list[dict] = []
    add(receptionist, "patient_last_name",  p_last)
    add(receptionist, "patient_first_name", p_first)
    add(receptionist, "patient_initial",    p_middle)
    add(receptionist, "patient_dob",        p_dob)
    add(receptionist, "patient_phone",      a.patient_phone or "")
    add(receptionist, "patient_address",    a.patient_address or "")
    add(receptionist, "patient_city",       a.patient_city or "")
    add(receptionist, "patient_state",      a.patient_state or "")
    add(receptionist, "patient_zip",        a.patient_zip or "")
    # Language / gender — not on the LARC assignment row, left blank
    # for the receptionist to fill from the chart.

    # Practice + provider identity — Bayer template now uses the same
    # `practice_*` naming convention as Nexplanon + Paragard. The
    # template also adds `practice_phone` + `practice_fax` + a separate
    # `shipping_address` field that defaults to the main practice
    # address (override per-key once a separate dock address is needed).
    add(receptionist, "practice_contact", settings.get("practice_contact"))
    add(receptionist, "practice_address", settings.get("practice_address"))
    add(receptionist, "practice_city",    settings.get("practice_city"))
    add(receptionist, "practice_state",   settings.get("practice_state"))
    add(receptionist, "practice_zip",     settings.get("practice_zip"))
    add(receptionist, "practice_phone",   settings.get("practice_contact_phone"))
    add(receptionist, "practice_fax",     settings.get("practice_fax"))
    # Shipping address stays blank unless Reception writes in a
    # different dock address at signing — defaulting it to the practice
    # address was wrong because the form is supposed to be empty when
    # shipping matches the office address above.
    # Bayer prints the provider name in "Last, First" format.
    add(receptionist, "provider_name_last_first", settings_last_first)
    add(receptionist, "provider_licenses", settings.get("provider_lic"))
    add(receptionist, "provider_dea",      settings.get("provider_dea"))
    add(receptionist, "provider_npi",      provider_npi_for_form)
    # Insurance — Bayer has separate prescription + medical sections.
    # We only know primary_insurance on the assignment; duplicate it
    # into both so the receptionist sees a sensible starting point.
    add(receptionist, "prescription_insurance_name", a.primary_insurance or "")
    add(receptionist, "medical_insurance_name",      a.primary_insurance or "")
    add(receptionist, "prescription_insurance_subscriber_id",
                      a.insurance_policy_no or "")
    add(receptionist, "medical_insurance_subscriber_id",
                      a.insurance_policy_no or "")
    add(receptionist, "medical_insurance_group",     a.insurance_group_no or "")

    # Device-family checkbox: tick the box for the specific Bayer device
    # this assignment is for (Mirena vs Skyla vs Kyleena). The template
    # has one checkbox per device; the provider signs the matching
    # signature line. Field IDs in BoldSign are case-sensitive — Skyla
    # is capitalized while the others are lowercase.
    device_name = (a.device.device_type.name if a.device and a.device.device_type
                    else None)
    if device_name is None and a.device_type_id:
        # Pharmacy-order before receive — look up the assignment's pinned type.
        from sqlalchemy.orm import object_session
        from app.models.larc import LarcDeviceType
        sess = object_session(a)
        if sess is not None:
            dt_row = (sess.query(LarcDeviceType)
                          .filter(LarcDeviceType.id == a.device_type_id)
                          .first())
            device_name = dt_row.name if dt_row else None
    bayer_checkbox_for = {
        "Kyleena": "kyleena",
        "Mirena":  "mirena",
        "Skyla":   "skyla",
    }
    cb_id = bayer_checkbox_for.get(device_name)
    if cb_id:
        receptionist.append({"id": cb_id, "value": "ON"})
    # Pharmacy checkbox — the Bayer template lists four specialty pharmacy
    # choices, each with its own checkbox. Match the assignment's
    # pharmacy.name to the corresponding checkbox. CVS Specialty's three
    # regional variants (Continental, Hawaii Neighbor, Hawaii Oahu) all
    # share the single `pharmacy_cvs` box on the form.
    if a.pharmacy_id:
        from sqlalchemy.orm import object_session
        from app.models.larc import LarcPharmacy
        sess = object_session(a)
        if sess is not None:
            pharm = (sess.query(LarcPharmacy)
                          .filter(LarcPharmacy.id == a.pharmacy_id).first())
            pharm_name = (pharm.name if pharm else "") or ""
            pharm_cb = None
            if "CVS Specialty" in pharm_name:
                pharm_cb = "pharmacy_cvs"
            elif "AllianceRx" in pharm_name or "Walgreens Prime" in pharm_name:
                pharm_cb = "pharmacy_AllianceRX"
            elif "Humana" in pharm_name:
                pharm_cb = "pharmacy_Humana"
            elif "Magellan" in pharm_name:
                pharm_cb = "pharmacy_Magellan"
            if pharm_cb:
                receptionist.append({"id": pharm_cb, "value": "ON"})

    # Provider credential checkbox — tick the row matching the inserting
    # provider's User.credential (MD/DO/NP/PA). Looked up via the
    # assignment's inserting_provider_email; falls back to no tick when
    # the email isn't a known clinician or has no credential set.
    cred_email = (a.inserting_provider_email or "").lower().strip()
    if cred_email:
        from sqlalchemy.orm import object_session
        from app.models.user import User
        sess = object_session(a)
        if sess is not None:
            u = sess.query(User).filter(User.email == cred_email).first()
            cred = (u.credential if u else None) or ""
            cred_box = {"MD": "md", "DO": "do", "NP": "np", "PA": "pa"}.get(cred.upper())
            if cred_box:
                receptionist.append({"id": cred_box, "value": "ON"})

    # ── Provider role: signatures only (drug-specific, provider picks one)
    provider: list[dict] = []  # no prefill — provider signs at signing time

    return {"Receptionist": receptionist, "Provider": provider}


# ─── Template registry ─────────────────────────────────────────────

@dataclass(frozen=True)
class _RoleSpec:
    name: str           # logical role: Receptionist | Patient | Provider
    role_index: int     # template's roleIndex (1-based, per template order)
    signer_order: int   # 1-based order in which they get the email


@dataclass(frozen=True)
class _TemplateSpec:
    template_id: str
    nice_name: str          # "Nexplanon" | "Paragard" | "Bayer (Mirena/Skyla/Kyleena)"
    roles: tuple             # tuple[_RoleSpec, ...]
    field_builder: object    # callable(a, settings, **kw) -> {role_name: [field…]}


_TEMPLATE_SPECS: dict[str, _TemplateSpec] = {
    NEXPLANON_TEMPLATE_ID: _TemplateSpec(
        template_id=NEXPLANON_TEMPLATE_ID,
        nice_name="Nexplanon",
        roles=(
            _RoleSpec("Receptionist", role_index=1, signer_order=1),
            _RoleSpec("Patient",       role_index=2, signer_order=2),
            _RoleSpec("Provider",      role_index=3, signer_order=3),
        ),
        field_builder=_build_nexplanon_fields,
    ),
    PARAGARD_TEMPLATE_ID: _TemplateSpec(
        template_id=PARAGARD_TEMPLATE_ID,
        nice_name="Paragard",
        roles=(
            # Paragard now has three roles. Template's roleIndex order
            # is Patient(1), Provider(2), Receptionist(3) — but our
            # workflow is Reception fills first, Patient signs, Provider
            # signs last.
            _RoleSpec("Patient",      role_index=1, signer_order=2),
            _RoleSpec("Provider",     role_index=2, signer_order=3),
            _RoleSpec("Receptionist", role_index=3, signer_order=1),
        ),
        field_builder=_build_paragard_fields,
    ),
    BAYER_TEMPLATE_ID: _TemplateSpec(
        template_id=BAYER_TEMPLATE_ID,
        nice_name="Bayer (Mirena/Skyla/Kyleena)",
        roles=(
            # Bayer's BoldSign template lists Provider first, Receptionist
            # second — so roleIndex follows that. signerOrder reflects our
            # workflow: Reception fills, then Provider signs.
            _RoleSpec("Provider",     role_index=1, signer_order=2),
            _RoleSpec("Receptionist", role_index=2, signer_order=1),
        ),
        field_builder=_build_bayer_fields,
    ),
}


# ─── Public send ───────────────────────────────────────────────────

def _resolve_provider(a: LarcAssignment) -> tuple[str, str, str]:
    """Return (email, display_name, npi) for the Provider signer role."""
    email = (a.inserting_provider_email or "").strip()
    name  = (a.inserting_provider_name  or "").strip()
    npi   = (a.inserting_provider_npi   or "").strip()
    if not email:
        email = _fallback_provider_email()
    if not name:
        name = _fallback_provider_name()
    return email, name, npi


def _resolve_app(a: LarcAssignment,
                  settings: dict[str, Optional[str]]) -> tuple[str, str]:
    """Return (name, npi) for the APP printed on the enrollment form.
    Per-assignment override beats PracticeConfig defaults; empty result
    is fine (the form just leaves those fields blank)."""
    name = (a.app_name or "").strip() or (settings.get("app_name") or "")
    npi  = (a.app_npi  or "").strip() or (settings.get("app_npi")  or "")
    return name, npi


def void_live_envelopes_for_assignment(
    db: Session,
    assignment: LarcAssignment,
    *,
    reason: str,
    actor_email: str,
) -> list[LarcEnrollmentEnvelope]:
    """Revoke every still-live BoldSign envelope tied to this assignment.

    Used when the assignment is cancelled (patient_no_show /
    patient_canceled / office_canceled) so the patient can't sign a
    pharmacy-enrollment form weeks later that would auto-fax the order
    to the dispensing pharmacy. Best-effort: BoldSign failures get
    recorded on the row (status='failed', last_fax_error=...) so the
    sweep can surface them, but they don't abort the cancellation.
    Returns the list of envelopes that were processed (voided or
    flagged failed). (Fable LARC audit C2.)
    """
    _LIVE = {"pending", "sent", "partially_signed", "signed"}
    rows = (db.query(LarcEnrollmentEnvelope)
              .filter(LarcEnrollmentEnvelope.assignment_id == assignment.id,
                      LarcEnrollmentEnvelope.status.in_(_LIVE))
              .all())
    if not rows:
        return []
    processed = []
    for env in rows:
        if not env.boldsign_envelope_id:
            env.status = "voided"
            env.voided_at = now_utc_naive()
            processed.append(env)
            continue
        if not _is_configured():
            env.last_fax_error = (
                f"void skipped — BoldSign not configured ({reason})")
            processed.append(env)
            continue
        try:
            with httpx.Client(
                base_url=API_BASE,
                headers={"X-API-KEY": _api_key()},
                timeout=10.0,
            ) as c:
                r = c.post(
                    "/v1/document/revoke",
                    params={"documentId": env.boldsign_envelope_id},
                    json={"message": reason[:120]},
                )
            if r.status_code >= 300:
                env.last_fax_error = (
                    f"BoldSign revoke {r.status_code}: {r.text[:200]}")
            else:
                env.status = "voided"
                env.voided_at = now_utc_naive()
        except Exception as e:
            env.last_fax_error = f"BoldSign revoke error: {e!r}"[:300]
        processed.append(env)
    return processed


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

    # Refuse to send if a live envelope already exists for this
    # assignment. Otherwise a double-click creates two BoldSign envelopes
    # → patient signs both → two completed webhooks → two pharmacy faxes
    # → duplicate $300-$1,100 device orders + duplicate PHI transmission.
    # (Fable LARC audit C2.) "Live" = anything not in a terminal
    # state; staff must explicitly void before re-sending.
    _LIVE_STATUSES = {"pending", "sent", "partially_signed", "signed", "faxed"}
    live_existing = (db.query(LarcEnrollmentEnvelope)
                       .filter(LarcEnrollmentEnvelope.assignment_id == assignment.id,
                               LarcEnrollmentEnvelope.status.in_(_LIVE_STATUSES))
                       .first())
    if live_existing:
        raise LarcEnrollmentError(
            f"A live enrollment envelope already exists for this assignment "
            f"(status={live_existing.status}, "
            f"id={(live_existing.boldsign_envelope_id or '')[:8]}…). "
            f"Void it before sending a new one.")

    # Prerequisites — resolve device_type. Pharmacy-order assignments
    # are created with device_id=NULL (the physical device hasn't shipped
    # yet) but device_type_id is pinned at creation so we can pick the
    # template up front.
    from app.models.larc import LarcDeviceType
    dt = None
    if assignment.device and assignment.device.device_type:
        dt = assignment.device.device_type
    elif assignment.device_type_id:
        dt = (db.query(LarcDeviceType)
                .filter(LarcDeviceType.id == assignment.device_type_id)
                .first())
    if not dt:
        raise LarcEnrollmentError(
            "Assignment has no device_type — set device_type_id on the "
            "assignment (or attach a device) before sending."
        )
    template_id = dt.enrollment_form_template
    if not template_id:
        raise LarcEnrollmentError(
            f"No BoldSign template ID configured for device type {dt.name!r}."
        )
    spec = _TEMPLATE_SPECS.get(template_id)
    if spec is None:
        raise LarcEnrollmentError(
            f"Enrollment template {template_id} (device {dt.name!r}) has no "
            "field map configured. Add a _TemplateSpec entry in "
            "app/services/larc_enrollment_sender.py."
        )

    needs_patient = any(r.name == "Patient" for r in spec.roles)
    if needs_patient and not (assignment.patient_email or "").strip():
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

    fields_by_role = spec.field_builder(
        assignment, settings,
        sent_by_email=sent_by_email,
        dispense=dispense,
        provider_contact_preference=provider_contact_preference,
        provider_name_for_form=provider_name,
        provider_npi_for_form=npi_for_form,
    )

    # Build the roles[] payload from the template spec. Each role gets the
    # right signer email + the prefill fields the builder produced for it.
    role_email_by_name = {
        "Receptionist": _receptionist_email(),
        "Patient":      (assignment.patient_email or "").strip(),
        "Provider":     provider_email,
    }
    role_signer_name = {
        "Receptionist": "WWC Reception",
        "Patient":      _friendly_name(assignment.patient_name),
        "Provider":     provider_name,
    }
    roles_payload = []
    for r in spec.roles:
        roles_payload.append({
            "signerName":  role_signer_name[r.name],
            "signerEmail": role_email_by_name[r.name],
            "signerType":  "Signer",
            "signerRole":  r.name,
            "signerOrder": r.signer_order,
            "roleIndex":   r.role_index,
            "existingFormFields": fields_by_role.get(r.name, []),
        })

    payload = {
        "title": (f"WWC — {dt.name} ({spec.nice_name}) Pharmacy Enrollment — "
                  f"{assignment.patient_name or 'Patient'}"),
        "message": (
            f"Please review and electronically sign the {spec.nice_name} "
            f"pharmacy enrollment form for {dt.name}. Once all signers "
            f"complete, the form will be faxed to the dispensing pharmacy."
        ),
        "roles": roles_payload,
        "enableSigningOrder": True,
        # Sender override (onBehalfOf=info@) intentionally removed —
        # BoldSign accepts the send but creates a document whose sender
        # identity is invalid until info@ is a confirmed team member.
        # Re-enable after info@ is verified in the BoldSign dashboard
        # under Team Members.
    }

    # Attach the patient's insurance-card image as an additional file
    # on the envelope. Pharmacies usually want to see the card next to
    # the enrollment form. BoldSign accepts a `files` array of
    # `data:<mime>;base64,...` URLs. Soft-fail if the blob is missing
    # so a deleted/lost upload doesn't block the envelope.
    if assignment.insurance_card_key:
        try:
            from app.services.storage import read_blob
            import base64
            card_bytes = read_blob(assignment.insurance_card_key)
            mime = (assignment.insurance_card_content_type
                    or "application/octet-stream")
            encoded = base64.b64encode(card_bytes).decode("ascii")
            payload["files"] = [f"data:{mime};base64,{encoded}"]
        except FileNotFoundError:
            log.warning("Insurance card blob missing for assignment %s — "
                        "sending envelope without attachment", assignment.id)
        except Exception:
            log.exception("Insurance card attach failed for assignment %s",
                          assignment.id)

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
        sent_at=now_utc_naive(),
        sent_by=sent_by_email,
    )
    db.add(env)
    if not assignment.enrollment_sent_at:
        assignment.enrollment_sent_at = now_utc_naive()

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


# ─── Webhook applier ───────────────────────────────────────────────

def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """BoldSign sends ISO 8601 timestamps. Tolerates None / blank."""
    if not value:
        return None
    try:
        # Strip a trailing Z so fromisoformat works on older Python.
        return datetime.fromisoformat(str(value).rstrip("Z"))
    except (ValueError, TypeError):
        return None


_STATUS_MAP = {
    "inprogress": "sent",
    "completed":  "signed",
    "declined":   "declined",
    "expired":    "voided",
    "revoked":    "voided",
}


def apply_webhook_event(db, env, data: dict) -> str:
    """Apply a BoldSign webhook payload to a LarcEnrollmentEnvelope row.

    Updates:
      - per-signer timestamps (receptionist/patient/provider)
      - overall status (sent | signed | declined | voided | faxed)
      - last_synced_at

    Returns the new `status` so the caller can log before/after.

    Side effect: when the envelope reaches Completed and hasn't been
    faxed yet, fires the auto-fax to LarcAssignment.pharmacy.fax. Any
    fax failure is recorded on the row (fax_status / last_fax_error) but
    does NOT raise — the webhook handler still returns 200 to BoldSign.
    """
    raw_status = (data.get("status") or data.get("Status") or "").lower()
    new_status = _STATUS_MAP.get(raw_status, raw_status or env.status)
    # Terminal-state guard. BoldSign retries + out-of-order deliveries can
    # send a late InProgress after Completed, or a stale Declined retry
    # after Signed. Without this, env.status used to flip
    # signed → sent (or signed → declined). Downstream dashboards then
    # show the wrong consent state for a form that was already faxed.
    # (Fable LARC audit H2.) Terminal-from-the-applier's POV is anything
    # that the workflow can't legitimately undo via a webhook: signed /
    # voided / declined / faxed / fax_failed. Voided/declined/faxed are
    # final business states; signed is a precursor to faxed and never
    # un-signs in BoldSign.
    _TERMINAL = {"signed", "voided", "declined", "faxed", "fax_failed"}
    if env.status in _TERMINAL and new_status not in _TERMINAL:
        log.info(
            "LARC envelope %s — webhook reports %r but envelope is already %r; "
            "ignoring regressive status change", env.boldsign_envelope_id,
            new_status, env.status)
        new_status = env.status
    env.status = new_status
    env.last_synced_at = now_utc_naive()

    if raw_status == "completed" and not env.signed_at:
        env.signed_at = (_parse_dt(data.get("completedDateTime")
                                    or data.get("completedAt"))
                          or now_utc_naive())
    elif raw_status == "declined" and not env.declined_at:
        env.declined_at = (_parse_dt(data.get("declinedDateTime")
                                      or data.get("declinedAt"))
                            or now_utc_naive())
    elif raw_status in ("revoked", "expired") and not env.voided_at:
        env.voided_at = (_parse_dt(data.get("revokedDateTime")
                                    or data.get("revokedAt"))
                          or now_utc_naive())

    # Per-signer timestamps — BoldSign sends `signerDetails` with one entry
    # per signer role; we mirror Reception / Patient / Provider onto the
    # row's three *_signed_at columns. First-write-wins so duplicate
    # webhook retries don't refresh the canonical signature time.
    signers = data.get("signerDetails") or data.get("SignerDetails") or []
    for s in signers:
        role = (s.get("signerRole") or s.get("SignerRole") or "").lower()
        s_status = (s.get("status") or s.get("Status") or "").lower()
        if s_status != "completed":
            continue
        signed_at = (_parse_dt(s.get("signedDateTime")
                                or s.get("completedDateTime"))
                      or now_utc_naive())
        if role == "receptionist" and not env.receptionist_signed_at:
            env.receptionist_signed_at = signed_at
        elif role == "patient" and not env.patient_signed_at:
            env.patient_signed_at = signed_at
        elif role == "provider" and not env.provider_signed_at:
            env.provider_signed_at = signed_at

    db.flush()  # let the fax service see the updated row

    # Trigger auto-fax to the pharmacy on completion. Imports lazy to
    # avoid a circular import (fax service pulls LarcAssignment models).
    #
    # Idempotency: BoldSign routinely re-delivers the same Completed
    # event (timeout retries, dashboard "Resend"). Without a guard, two
    # concurrent deliveries both see faxed_at IS NULL, both fetch the
    # PDF, and both fax PHI to the pharmacy. The atomic claim below
    # transitions fax_status to "in_progress" only when it isn't
    # already; PostgreSQL row-level locking serializes the two UPDATEs,
    # so exactly one webhook wins the slot and the loser sees
    # rowcount=0 and skips the fax call entirely.
    if raw_status == "completed" and not env.faxed_at:
        claimed = db.query(LarcEnrollmentEnvelope).filter(
            LarcEnrollmentEnvelope.id == env.id,
            LarcEnrollmentEnvelope.faxed_at.is_(None),
            or_(LarcEnrollmentEnvelope.fax_status.is_(None),
                LarcEnrollmentEnvelope.fax_status.notin_(
                    ("in_progress", "Queued", "Sent", "Delivered"))),
        ).update(
            {LarcEnrollmentEnvelope.fax_status: "in_progress"},
            synchronize_session=False,
        )
        db.flush()
        if claimed == 0:
            log.info("LARC envelope %s — auto-fax already claimed by "
                     "another webhook delivery; skipping",
                     env.boldsign_envelope_id)
        else:
            db.refresh(env)   # pull the new fax_status into the ORM copy
            try:
                from app.services.larc.pharmacy_fax import fax_envelope
                fax_envelope(db, env, by_email="system:webhook")
            except Exception as exc:
                # Swallow — webhook handler logs + returns 200 regardless.
                # The failure is recorded on the row by fax_envelope itself
                # (last_fax_error / fax_status), or here for unexpected
                # exits.
                env.last_fax_error = str(exc)
                env.fax_status = "fax_failed"
                log.exception("LARC auto-fax raised unexpectedly")

    # Advance the assignment workflow once the envelope is signed (and,
    # if the auto-fax succeeded, mark request_faxed). Without this, the
    # dashboard's needs_enrollment / needs_fax buckets stayed lit even
    # after the webhook completed successfully, training staff to ignore
    # them or manually re-fax. (Fable LARC audit M7.)
    if env.signed_at:
        assignment = env.assignment if hasattr(env, "assignment") else None
        if assignment is None:
            assignment = (db.query(LarcAssignment)
                            .filter(LarcAssignment.id == env.assignment_id)
                            .first())
        if assignment is not None:
            if not assignment.enrollment_signed_at:
                assignment.enrollment_signed_at = env.signed_at
            # request_faxed_at reflects the successful pharmacy fax; key
            # off env.faxed_at (set by fax_envelope on Sent/Delivered).
            if env.faxed_at and not assignment.request_faxed_at:
                assignment.request_faxed_at = env.faxed_at

    return env.status
