"""Surgery settings registry.

Every runtime-tunable surgery value lives here with its default equal to
the previously hardcoded value, so a missing SurgeryConfig row always
means "behave exactly as before". Reads go through cfg(); writes go
through PUT /surgery/config (surgery_config.py), which validates against
this registry.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.surgery_config import SurgeryConfig

log = logging.getLogger(__name__)

SETTINGS_DEFAULTS: dict[str, Any] = {
    # ── pre-existing keys (moved from surgery_config.CONFIG_DEFAULTS) ──
    "office_full_threshold":     6,
    "office_lookahead_days":     6,
    "hospital_lookahead_days":  14,
    "reminder_lead_days":        [3, 1],

    # ── alerts & windows (previously hardcoded) ──
    "critical_overdue_hours":   48,    # surgery.py stuck-list red threshold
    "labs_alert_window_days":    7,    # surgery.py labs alert
    "post_op_docs_alert_days":   5,    # surgery.py op-notes alert
    "unresponsive_after_days":  30,    # was UNRESPONSIVE_AFTER_DAYS
    "preop_valid_days":        180,    # was PREOP_VALID_DAYS
    "schedule_horizon_days":   180,    # block_schedule materialization window
    "completed_window_days":    30,    # dashboard "completed last N days"

    # ── steps engine (consumed by step_engine.py in a later task) ──
    "step_expected_days_hospital": None,   # None → use catalog defaults
    "step_expected_days_office":   None,
    "step_titles_hospital":        None,
    "step_titles_office":          None,

    # ── structured configs (None → code defaults in their modules) ──
    "post_op_schedules":           None,   # post_op_schedule.py defaults
    "capacity_rules":              None,   # block_schedule.py defaults

    # ── intake option lists (editable in Surgery Settings) ──
    "clearance_types":             ["None", "EKG", "Hematology", "Cardiology", "Pulmonology", "General"],
    "surgery_device_types":        ["None", "Benesta", "Liletta", "Mirena", "Paragard", "Skyla", "Kyleena"],
    "assistant_surgeons":          ["None", "Dr. Gillespie"],

    # ── payer-ID → insurance-company resolution (order-prefill) ──
    # Maps an electronic payer ID (string) extracted from a surgery order to
    # one of the INSURANCE_COMPANIES picklist values so insurance prefills.
    #
    # Seeded with high-confidence national EDI payer IDs + one verified from a
    # real WWC order (75191). Payer IDs CAN vary by clearinghouse, so the
    # coordinator should confirm the prefilled company on each order. WWC's
    # orders use Change Healthcare / Emdeon-style IDs, so the Maryland
    # Medicaid MCO entries below are Change-Healthcare-aligned and should be
    # confirmed on the first real order for each MCO. Keys may be alphanumeric
    # (e.g. "WLPNT", "128MD") and are looked up case-insensitively. Note: UHC
    # Community Plan shares payer ID 87726 with commercial UnitedHealthcare,
    # so 87726 resolves to "UnitedHealthcare" (a payer ID maps to one
    # company). CareFirst plan variants still use clearinghouse-specific IDs
    # that should be added from real orders rather than guessed. Editable in
    # Surgery Settings → Clearances & Devices → Payer ID → Insurance.
    "payer_id_insurance_map": {
        "75191": "Blue Cross & Blue Shield PPO",   # BCBS Administrators PPO — verified on a WWC order
        "60054": "Aetna",                          # national Aetna
        "62308": "Cigna",                          # national Cigna
        "87726": "UnitedHealthcare",               # national UnitedHealthcare (also UHC Community Plan)
        "61101": "Humana",                         # national Humana
        "00580": "CareFirst BlueChoice",           # CareFirst BCBS of Maryland — verify per clearinghouse
        "12302": "Medicare",                       # Novitas JL (MD Part B) — verify per clearinghouse

        # ── Maryland Medicaid MCOs (Change-Healthcare-aligned) ──
        "52189": "Priority Partners (MCO)",          # Johns Hopkins HealthCare — consistent across clearinghouses
        "22348": "Maryland Physicians Care (MCO)",   # Emdeon/Change Healthcare
        "26375": "Wellpoint Maryland (MCO)",         # legacy Amerigroup ID (still valid)
        "WLPNT": "Wellpoint Maryland (MCO)",         # current Wellpoint ID
        "RP063": "MedStar Family Choice (MCO)",      # Change Healthcare
        "128MD": "Aetna Better Health (MCO)",        # universal ABHMD ID
    },
}


def cfg(db: Session, key: str) -> Any:
    """Read one setting: DB row if present, else registry default.
    Never raises for DB problems — falls back to the default."""
    if key not in SETTINGS_DEFAULTS:
        raise KeyError(f"Unknown surgery setting: {key}")
    try:
        row = db.query(SurgeryConfig).filter(SurgeryConfig.key == key).first()
        if row is not None and row.value is not None:
            return row.value
    except Exception:                                    # pragma: no cover
        log.warning("surgery settings read failed for %s; using default", key)
    return SETTINGS_DEFAULTS[key]
