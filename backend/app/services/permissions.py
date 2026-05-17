"""Permission catalog + default group seed.

This file is the single source of truth for what permissions exist. Adding a
new permission means editing this file and adding `Depends(require_permission(...))`
to the relevant endpoint(s) — that's the entire migration.

Groups (named bundles of permissions) live in the DB and are admin-editable.
The seed in DEFAULT_GROUPS is applied once on first migration; after that,
admins can edit memberships and group permissions in the Groups admin page.
"""
from __future__ import annotations

from typing import Dict, List, Set


# ─────────────────────────────────────────────────────────────────────
# Catalog — every permission the app recognizes. Edit here, then guard
# the relevant endpoint with require_permission("...").

PERMISSIONS: Dict[str, str] = {
    # Patient
    "patient:read":        "View patient demographics, contact info, insurance",
    "patient:create":      "Register a new patient",
    "patient:edit":        "Update patient demographics",

    # Clinical chart
    "chart:read":          "View clinical chart and encounter notes",
    "chart:edit":          "Add to or modify chart entries",
    "chart:sign":          "Sign off on encounters (provider)",

    # Documents
    "document:read":       "View patient documents",
    "document:upload":     "Upload patient documents",
    "document:delete":     "Delete patient documents",

    # Intake
    "intake:read":         "View intake forms",
    "intake:edit":         "Fill out / update intake forms",

    # Schedule
    "schedule:read":       "View appointments",
    "schedule:edit":       "Book, cancel, reschedule appointments",

    # Billing — claims & AR
    "claim:read":          "View claims and AR queue",
    "claim:edit":          "Edit claim details, status, assignment, notes",
    "claim:settle_line":   "Enter EOB detail per service line",
    "claim:writeoff":      "Write off a claim",
    "claim:appeal":        "Draft and file appeal letters",
    "denial:work":         "Work the denials queue",

    # Billing — money
    "payment:post":        "Post insurance payments and allocate to claims",
    "eob:edit":            "Edit EOB details on a claim",
    "adjustment_code:edit":"Manage CARC/RARC notes and combo cache",

    # Bank reconciliation & reports
    "bankrecon:read":      "View bank reconciliation page",
    "bankrecon:generate":  "Generate BAI files from CSV imports",
    "report:financial":    "View financial reports / dashboards",
    "report:operational":  "View operational reports (no-shows, throughput)",

    # Communication
    "fax:read":            "View fax inbox / outbox",
    "fax:send":            "Send faxes (appeals, records requests)",

    # Administrative
    "user:manage":         "Create / edit / delete users; assign groups",
    "audit:read":          "View the audit log",
    "checklist:manage":    "Manage checklist templates and assignments",
    "training:authorize":  "Authorize trainers and revoke training certifications",

    # Recalls
    "recall:work":         "Work the recall queue — view, log calls, update outcomes",
    "recall:manage":       "Manage recall scripts, suppressions, and bulk imports",

    # Surgery scheduling
    "surgery:read":        "View the surgery scheduling dashboard and detail pages",
    "surgery:work":        "Upload orders, advance milestones, schedule cases",
    "surgery:cancel":      "Cancel surgeries, move to hold, mark unresponsive",
    "surgery:manage":      "Manage block schedule, milestone templates, klara templates",

    # LARC device inventory + tracking
    "larc:read":           "View LARC dashboard, devices, assignments, audit log",
    "larc:work":           "Manage assignments, log benefits, advance milestones, record outcomes",
    "larc:checkout":       "Request a device check-out for insertion (MAs, providers)",
    "larc:approve":        "Approve / deny flagged check-out requests (managers)",
    "larc:manage":         "Manage device-type catalog, pharmacy directory, inventory CRUD",

    # Pellet inventory + DEA-compliant audit
    "pellet:read":         "View pellet dashboard, inventory, audit log",
    "pellet:work":         "Receive shipments, transfer between locations, count, dispose",
    "pellet:manage":       "Manage dose-type catalog, reorder thresholds, manual",

    "system:admin":        "Break-glass / settings / impersonation",
}


ALL_PERMISSIONS: frozenset = frozenset(PERMISSIONS.keys())


# ─────────────────────────────────────────────────────────────────────
# Default groups — seeded once on first migration. After that, admins
# manage groups + memberships in-app. `system_protected=True` blocks
# deletion (members can still be edited).

DEFAULT_GROUPS: List[Dict] = [
    {
        "name": "Admin",
        "description": "Full access to every feature including user management.",
        "system_protected": True,
        "permissions": sorted(ALL_PERMISSIONS),
    },
    {
        "name": "Office Manager",
        "description": "Broad operational access — all billing and admin reporting, "
                       "but not user management.",
        "system_protected": False,
        "permissions": [
            "patient:read", "patient:create", "patient:edit",
            "chart:read",
            "document:read", "document:upload", "document:delete",
            "intake:read", "intake:edit",
            "schedule:read", "schedule:edit",
            "claim:read", "claim:edit", "claim:settle_line",
            "claim:writeoff", "claim:appeal",
            "denial:work",
            "payment:post",
            "eob:edit",
            "adjustment_code:edit",
            "bankrecon:read", "bankrecon:generate",
            "report:financial", "report:operational",
            "fax:read", "fax:send",
            "audit:read",
            "checklist:manage",
            "training:authorize",
            "recall:work", "recall:manage",
            "surgery:read", "surgery:work", "surgery:cancel", "surgery:manage",
            "larc:read", "larc:work", "larc:approve", "larc:manage",
            "pellet:read", "pellet:work", "pellet:manage",
        ],
    },
    {
        "name": "Provider",
        "description": "Sign clinical encounters. View patients, charts, documents.",
        "system_protected": False,
        "permissions": [
            "patient:read",
            "chart:read", "chart:edit", "chart:sign",
            "document:read", "document:upload",
            "intake:read", "intake:edit",
            "schedule:read", "schedule:edit",
            "surgery:read",
            "larc:read", "larc:checkout",
        ],
    },
    {
        "name": "Medical Assistant",
        "description": "Clinical chart edits, intake, vitals. Cannot sign.",
        "system_protected": False,
        "permissions": [
            "patient:read", "patient:edit",
            "chart:read", "chart:edit",
            "document:read", "document:upload",
            "intake:read", "intake:edit",
            "schedule:read",
            "recall:work",
            "surgery:read",
            "larc:read", "larc:work", "larc:checkout",
        ],
    },
    {
        "name": "Front Desk",
        "description": "Demographics, scheduling, intake, eligibility lookups.",
        "system_protected": False,
        "permissions": [
            "patient:read", "patient:create", "patient:edit",
            "document:read", "document:upload",
            "intake:read", "intake:edit",
            "schedule:read", "schedule:edit",
            "claim:read",
            "report:operational",
            "fax:read",
            "recall:work",
            "surgery:read", "surgery:work",
            "larc:read",
        ],
    },
    {
        "name": "Billing — Coding",
        "description": "Code yesterday's encounters, query providers, edit claim codes.",
        "system_protected": False,
        "permissions": [
            "patient:read",
            "chart:read",
            "claim:read", "claim:edit",
            "adjustment_code:edit",
        ],
    },
    {
        "name": "Billing — Payments",
        "description": "Post ERAs, settle EOBs line by line, reconcile bank deposits.",
        "system_protected": False,
        "permissions": [
            "patient:read",
            "claim:read", "claim:settle_line",
            "payment:post",
            "eob:edit",
            "adjustment_code:edit",
            "bankrecon:read", "bankrecon:generate",
        ],
    },
    {
        "name": "Billing — Denials",
        "description": "Work denials, file appeals, send appeal faxes.",
        "system_protected": False,
        "permissions": [
            "patient:read",
            "claim:read", "claim:edit", "claim:appeal",
            "denial:work",
            "adjustment_code:edit",
            "fax:read", "fax:send",
        ],
    },
    {
        "name": "Audit Reader",
        "description": "Read-only access to the audit log. For compliance reviews.",
        "system_protected": False,
        "permissions": [
            "audit:read",
            "patient:read",
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────
# Migration: map old (group, practice_role) → list of new group names.
# Used once when migrating existing users into the new model.

LEGACY_MIGRATION = {
    # group=admin always implies the Admin group
    "admin": ["Admin"],
    # group=billing without a practice_role lands in Billing — Payments
    # as a sane default; admin can refine.
    "billing": ["Billing — Payments"],
    # group=clinical without a practice_role lands in Medical Assistant
    # as the broadest clinical default; admin can refine.
    "clinical": ["Medical Assistant"],
}

PRACTICE_ROLE_TO_GROUP = {
    "office_manager":    "Office Manager",
    "provider":          "Provider",
    "ma":                "Medical Assistant",
    "front_desk":        "Front Desk",
    "billing_coding":    "Billing — Coding",
    "billing_payments":  "Billing — Payments",
    "billing_denials":   "Billing — Denials",
    # CaribCall users default to Front Desk per Oliver's call (2026-05-07).
    "caribcall":         "Front Desk",
}


def legacy_groups_for_user(group_value: str | None,
                            practice_role: str | None) -> List[str]:
    """Return the list of seed group names this user should be migrated into.

    Always uppercase-folded values from the legacy enum — `group` is the
    coarse buckets (admin/billing/clinical), `practice_role` is the finer
    role from the checklist work.
    """
    out: List[str] = []
    if group_value:
        out.extend(LEGACY_MIGRATION.get(group_value.lower(), []))
    if practice_role:
        mapped = PRACTICE_ROLE_TO_GROUP.get(practice_role)
        if mapped and mapped not in out:
            out.append(mapped)
    return out


# ─────────────────────────────────────────────────────────────────────
# Effective permissions — runtime computation.
# Imported lazily by callers to avoid circular imports with models.

def effective_permissions(user) -> Set[str]:
    """Compute the set of permissions a User has in effect right now.

    Effective = union of permissions from all groups the user belongs to,
                plus permissions_extra,
                minus permissions_revoked.

    `user` must be the SQLAlchemy User row (with `groups` relationship loaded)
    or anything that exposes the same shape. Returns an empty set for an
    unrecognized / deleted user.
    """
    if user is None:
        return set()
    perms: Set[str] = set()
    # Walk groups → group_permissions
    for grp in (user.groups or []):
        for gp in (grp.permissions or []):
            perms.add(gp.permission)
    extras = user.permissions_extra or []
    revoked = user.permissions_revoked or []
    perms |= set(extras)
    perms -= set(revoked)
    # Drop anything no longer in the catalog (defensive against stale data)
    return perms & ALL_PERMISSIONS
