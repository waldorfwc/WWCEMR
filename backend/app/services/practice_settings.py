"""Practice-wide settings registry.

Wraps the existing `practice_config` key/value table with a typed schema
of fields the LARC pharmacy enrollment forms (and other forms going
forward) need to prefill: practice address/contact, provider identity,
advanced-practice-provider identity, etc.

The PracticeConfig table can also hold ad-hoc keys written by other
modules (pellet inventory lock, checklist owner email, etc.); this
module ONLY covers the LARC-enrollment-related keys via the
PRACTICE_SETTING_REGISTRY below. Other keys are untouched.

Storage strategy: PracticeConfig.value is a single VARCHAR(500) — fine
for everything in the registry (longest plausible value is the street
address). Validation lives in the registry, not the DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.practice_config import PracticeConfig
from app.services.audit_service import log_action


# ─── Registry ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class SettingSpec:
    key: str
    group: str       # "Practice" | "Provider" | "Advanced Practice Provider"
    label: str
    help: str        # Inline help text under the input
    secret: bool = False  # Reserved for future masking; we don't mask today


PRACTICE_SETTING_REGISTRY: list[SettingSpec] = [
    # ── Practice ───────────────────────────────────────────────────
    SettingSpec("practice_name",          "Practice", "Practice Name",
                "Legal practice name as it appears on enrollment forms."),
    SettingSpec("practice_address",       "Practice", "Street Address",
                "Street number + street. City/state/zip are separate."),
    SettingSpec("practice_city",          "Practice", "City", ""),
    SettingSpec("practice_state",         "Practice", "State",
                "Two-letter postal abbreviation (e.g., MD)."),
    SettingSpec("practice_zip",           "Practice", "ZIP", ""),
    SettingSpec("practice_taxid",         "Practice", "Tax ID (EIN)",
                "Federal EIN. Required on most enrollment forms."),
    SettingSpec("practice_medicaid_lic",  "Practice", "Medicaid License #",
                "State Medicaid provider number."),
    SettingSpec("practice_contact",       "Practice", "Contact Person",
                "Name of the person pharmacies should call for follow-up."),
    SettingSpec("practice_contact_phone", "Practice", "Contact Phone",
                "Direct line for the practice contact above."),
    SettingSpec("practice_fax",           "Practice", "Practice Fax",
                "Fax number printed on enrollment forms."),
    SettingSpec("practice_email",         "Practice", "Practice Email",
                "Shared inbox email shown on forms (e.g., info@…)."),

    # ── Provider ───────────────────────────────────────────────────
    SettingSpec("provider_first_name",    "Provider", "First Name", ""),
    SettingSpec("provider_last_name",     "Provider", "Last Name", ""),
    SettingSpec("provider_npi",           "Provider", "NPI",
                "10-digit National Provider Identifier."),
    SettingSpec("provider_name",          "Provider", "Printed Name on Signature",
                "How the provider's name should print on the signature line "
                "(e.g., 'Dr. Aryian Cooke')."),

    # ── Advanced Practice Provider (optional) ──────────────────────
    SettingSpec("app_name",               "Advanced Practice Provider", "APP Name",
                "Advanced Practice Provider's full name. Leave blank if N/A."),
    SettingSpec("app_npi",                "Advanced Practice Provider", "APP NPI",
                "APP's 10-digit NPI. Leave blank if N/A."),
]


REGISTRY_KEYS = {s.key for s in PRACTICE_SETTING_REGISTRY}


# ─── Read / write ──────────────────────────────────────────────────

def get_all(db: Session) -> dict[str, Optional[str]]:
    """Return {key: value or None} for every registry key, even if no
    PracticeConfig row exists yet — callers can render the form without
    worrying about missing keys."""
    rows = (db.query(PracticeConfig)
              .filter(PracticeConfig.key.in_(REGISTRY_KEYS))
              .all())
    by_key = {r.key: r.value for r in rows}
    return {k: by_key.get(k) for k in REGISTRY_KEYS}


def get_value(db: Session, key: str) -> Optional[str]:
    if key not in REGISTRY_KEYS:
        raise KeyError(f"unknown practice setting key: {key!r}")
    row = db.query(PracticeConfig).filter(PracticeConfig.key == key).first()
    return row.value if row else None


def set_value(db: Session, key: str, value: Optional[str], *,
               actor_email: str) -> str:
    """Upsert one key. Empty string clears the value (stored as NULL).
    Audits the change with before/after."""
    if key not in REGISTRY_KEYS:
        raise KeyError(f"unknown practice setting key: {key!r}")
    new_val = (value or "").strip() or None
    row = db.query(PracticeConfig).filter(PracticeConfig.key == key).first()
    old_val = row.value if row else None
    if row:
        row.value = new_val
    else:
        db.add(PracticeConfig(key=key, value=new_val))
    log_action(
        db, "PRACTICE_SETTING_UPDATED", "practice_config",
        resource_id=key,
        user_name=actor_email,
        description=(
            f"Set {key} = {new_val!r}" if new_val != old_val
            else f"Touched {key} (no change)"
        ),
        old_values={key: old_val},
        new_values={key: new_val},
    )
    db.commit()
    return new_val or ""
