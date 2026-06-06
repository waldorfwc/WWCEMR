"""Module + Tier catalog. Single source of truth for the permission structure.

Per the design spec (docs/superpowers/specs/2026-06-06-permissions-redesign-design.md):
  - 15 modules, declared in `Module`
  - 5 tiers, declared in `Tier` (NONE/VIEW/WORK/MANAGE/ADMIN/SUPER_ADMIN)
  - Each module declares what falls under Manage; View and Work are universal

Adding a new module = one new Module enum value + one MODULE_REGISTRY entry.
"""
from dataclasses import dataclass
from enum import Enum, IntEnum


class Module(str, Enum):
    """Slug identifiers for the 15 modules. The string value is what gets
    stored in `group_module_tiers.module` and `user_module_overrides.module`."""

    CHART                       = "chart"
    ACTIVE_AR                   = "active_ar"
    BANK_RECON                  = "billing_bank_recon"
    MISSING_CHARGES             = "billing_missing_charges"
    INSURANCE_DOCS              = "billing_insurance_docs"
    INSURANCE_CONTACTS          = "billing_insurance_contacts"
    RECALL                      = "recall"
    SURGERY                     = "surgery"
    LARC                        = "device_larc"
    OFFICE_PROCEDURES           = "device_office_procedures"
    PELLETS                     = "pellets"
    REPUTATION                  = "reputation"
    TRAINING                    = "training"
    MY_CHECKLIST                = "my_checklist"
    AUDIT_LOG                   = "audit_log"


class Tier(IntEnum):
    """Ordinal permission tiers. Higher includes all powers of lower.

    Per-module: VIEW < WORK < MANAGE < ADMIN.
    Global:     SUPER_ADMIN (single boolean on User, not stored per-module).
    """

    NONE         = 0
    VIEW         = 10
    WORK         = 20
    MANAGE       = 30
    ADMIN        = 40
    SUPER_ADMIN  = 50


@dataclass(frozen=True)
class ModuleSpec:
    """Human-readable description of a module + what its Manage tier covers."""

    label: str
    description: str
    manage_means: str


MODULE_REGISTRY: dict[Module, ModuleSpec] = {
    Module.CHART: ModuleSpec(
        label="Chart",
        description="Patient demographics, clinical history, encounters, recalls.",
        manage_means=(
            "Merge duplicate charts; configure problem-list templates; "
            "delete chart entries."
        ),
    ),
    Module.ACTIVE_AR: ModuleSpec(
        label="Active AR",
        description="Claim queue, payments, denials, appeals, ERA posting.",
        manage_means=(
            "Bulk write-off; configure denial codes & workflow states; "
            "delete claims."
        ),
    ),
    Module.BANK_RECON: ModuleSpec(
        label="Billing – Bank Recon",
        description="Bank reconciliation workflow.",
        manage_means=(
            "Configure recon rules; resolve overlap exceptions; "
            "delete reconciled records."
        ),
    ),
    Module.MISSING_CHARGES: ModuleSpec(
        label="Billing – Missing Charges",
        description="Provider charge-capture review.",
        manage_means=(
            "Issue provider portal tokens; bulk-mark complete; delete charges."
        ),
    ),
    Module.INSURANCE_DOCS: ModuleSpec(
        label="Billing – Insurance Documents",
        description="Insurance correspondence and billing documents.",
        manage_means=(
            "Hard-delete documents; bulk-assign; configure classifications."
        ),
    ),
    Module.INSURANCE_CONTACTS: ModuleSpec(
        label="Billing – Insurance Contacts",
        description="Payer contact directory.",
        manage_means=(
            "Bulk import; delete contacts; configure source rules."
        ),
    ),
    Module.RECALL: ModuleSpec(
        label="Recall",
        description="Patient recall lists and outreach.",
        manage_means=(
            "Configure recall rules; bulk-schedule; delete recall lists."
        ),
    ),
    Module.SURGERY: ModuleSpec(
        label="Surgery",
        description="Surgery scheduling, consent, fee schedule, block calendar.",
        manage_means=(
            "Configure block schedules / fee schedule / consent templates / "
            "surgery types; delete surgeries."
        ),
    ),
    Module.LARC: ModuleSpec(
        label="Device Tracking – LARC",
        description="IUD/implant pharmacy and checkout workflow.",
        manage_means=(
            "Bulk-import devices; configure inventory rules; delete devices."
        ),
    ),
    Module.OFFICE_PROCEDURES: ModuleSpec(
        label="Device Tracking – Office Procedures",
        description="Office-procedure device tracking.",
        manage_means=(
            "Bulk-import devices; configure inventory rules; delete devices."
        ),
    ),
    Module.PELLETS: ModuleSpec(
        label="Pellets",
        description="Pellet inventory + visits (DEA Schedule III).",
        manage_means=(
            "Configure lots & dose schedules; configure Smartsheet sync; "
            "delete adjustments (within DEA constraints)."
        ),
    ),
    Module.REPUTATION: ModuleSpec(
        label="Reputation Management",
        description="Review portal and patient feedback.",
        manage_means=(
            "Configure review portal; configure response templates; delete reviews."
        ),
    ),
    Module.TRAINING: ModuleSpec(
        label="Training",
        description="Training modules and completion tracking.",
        manage_means=(
            "Author training modules; assign training paths; "
            "mark complete on behalf of others."
        ),
    ),
    Module.MY_CHECKLIST: ModuleSpec(
        label="My Checklist",
        description="Personal task lists. Own-list access is implicit.",
        manage_means=(
            "Assign tasks to other users; configure recurring tasks."
        ),
    ),
    Module.AUDIT_LOG: ModuleSpec(
        label="Audit Log",
        description="HIPAA audit trail. Append-only.",
        manage_means=(
            "Export audit data; configure retention policy. "
            "Rows are append-only — no deletion ever."
        ),
    ),
}
