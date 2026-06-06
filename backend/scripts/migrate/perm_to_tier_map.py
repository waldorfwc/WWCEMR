"""Translation table: legacy verb:resource permission → new (Module, Tier).

Single source of truth for the Phase 2 migration. Reviewed inline in the
design spec §Migration / Translation cheat-sheet.

A user/group that holds an old permission string ends up granted AT LEAST
the listed tier on each listed module. The Phase 2 script applies max()
across all permissions a group holds.
"""
from app.permissions.catalog import Module, Tier


# Old permission string → list of (Module, Tier) it implies.
PERM_TO_TIER: dict[str, list[tuple[Module, Tier]]] = {
    # ─── Chart bundle ───────────────────────────────────────────────
    "patient:read":   [(Module.CHART, Tier.VIEW)],
    "patient:create": [(Module.CHART, Tier.WORK)],
    "patient:edit":   [(Module.CHART, Tier.WORK)],
    "chart:read":     [(Module.CHART, Tier.VIEW)],
    "chart:edit":     [(Module.CHART, Tier.WORK)],
    "chart:sign":     [(Module.CHART, Tier.WORK)],
    # document:read/upload/delete spans both chart-side and billing-side
    # routers. We grant tier on BOTH modules; the per-router gate after
    # cutover (Phase 3) determines which one actually applies at the
    # endpoint level.
    "document:read":    [(Module.CHART, Tier.VIEW),
                          (Module.INSURANCE_DOCS, Tier.VIEW)],
    "document:upload":  [(Module.CHART, Tier.WORK),
                          (Module.INSURANCE_DOCS, Tier.WORK)],
    "document:delete":  [(Module.CHART, Tier.MANAGE),
                          (Module.INSURANCE_DOCS, Tier.MANAGE)],
    "intake:read":      [(Module.CHART, Tier.VIEW)],
    "intake:edit":      [(Module.CHART, Tier.WORK)],

    # ─── Faxing — folded into owning module per design ──────────────
    "fax:read": [],
    "fax:send": [],

    # ─── Active AR family ───────────────────────────────────────────
    # claim:read historically gated more than just Active AR — Missing
    # Charges, Bank Recon, and Insurance Documents/Contacts also lived
    # behind it. Bridge to View on each billing module so anyone who
    # currently reads claims keeps seeing the rest of billing.
    "claim:read":         [(Module.ACTIVE_AR, Tier.VIEW),
                           (Module.MISSING_CHARGES, Tier.VIEW),
                           (Module.INSURANCE_DOCS, Tier.VIEW),
                           (Module.INSURANCE_CONTACTS, Tier.VIEW),
                           (Module.BANK_RECON, Tier.VIEW)],
    "claim:edit":         [(Module.ACTIVE_AR, Tier.WORK)],
    "claim:appeal":       [(Module.ACTIVE_AR, Tier.WORK)],
    "claim:settle_line":  [(Module.ACTIVE_AR, Tier.WORK)],
    "claim:writeoff":     [(Module.ACTIVE_AR, Tier.MANAGE)],
    "payment:post":       [(Module.ACTIVE_AR, Tier.WORK)],
    "payment:void":       [(Module.ACTIVE_AR, Tier.MANAGE)],
    "denial:work":        [(Module.ACTIVE_AR, Tier.WORK)],
    "eob:edit":           [(Module.ACTIVE_AR, Tier.WORK)],
    "adjustment_code:edit": [(Module.ACTIVE_AR, Tier.MANAGE)],
    "report:operational": [(Module.ACTIVE_AR, Tier.MANAGE)],

    # ─── Bank Recon ─────────────────────────────────────────────────
    "bankrecon:read":     [(Module.BANK_RECON, Tier.VIEW)],
    "bankrecon:generate": [(Module.BANK_RECON, Tier.WORK)],

    # ─── Audit Log ──────────────────────────────────────────────────
    "audit:read": [(Module.AUDIT_LOG, Tier.VIEW)],

    # ─── Reports / Dashboard → billing manager seat ─────────────────
    "report:financial": [(Module.ACTIVE_AR, Tier.MANAGE)],

    # ─── Surgery ────────────────────────────────────────────────────
    "surgery:read":   [(Module.SURGERY, Tier.VIEW)],
    "surgery:work":   [(Module.SURGERY, Tier.WORK)],
    "surgery:cancel": [(Module.SURGERY, Tier.WORK)],
    "surgery:manage": [(Module.SURGERY, Tier.MANAGE)],
    "schedule:read":  [(Module.SURGERY, Tier.VIEW)],
    "schedule:edit":  [(Module.SURGERY, Tier.WORK)],

    # ─── Recall ─────────────────────────────────────────────────────
    "recall:work":   [(Module.RECALL, Tier.WORK)],
    "recall:manage": [(Module.RECALL, Tier.MANAGE)],

    # ─── My Checklist ───────────────────────────────────────────────
    "checklist:manage": [(Module.MY_CHECKLIST, Tier.MANAGE)],

    # ─── Training ───────────────────────────────────────────────────
    "training:authorize": [(Module.TRAINING, Tier.MANAGE)],

    # ─── Device Tracking – LARC ─────────────────────────────────────
    "larc:read":     [(Module.LARC, Tier.VIEW)],
    "larc:edit":     [(Module.LARC, Tier.WORK)],
    "larc:work":     [(Module.LARC, Tier.WORK)],
    "larc:checkout": [(Module.LARC, Tier.WORK)],
    "larc:approve":  [(Module.LARC, Tier.MANAGE)],
    "larc:manage":   [(Module.LARC, Tier.MANAGE)],

    # ─── Pellets ────────────────────────────────────────────────────
    "pellet:read":   [(Module.PELLETS, Tier.VIEW)],
    "pellet:work":   [(Module.PELLETS, Tier.WORK)],
    "pellet:manage": [(Module.PELLETS, Tier.MANAGE)],

    # ─── Admin (cross-module) — handled separately via ADMIN_PERMS ──
    "user:manage":  [],
    "system:admin": [],
}


# Old permission strings whose holders should become per-module Admins.
# Both `user:manage` and `system:admin` are sysop roles — grants the
# ADMIN tier on every module so existing admins keep their reach.
ADMIN_PERMS: set[str] = {"user:manage", "system:admin"}
