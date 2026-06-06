# Permissions Redesign — Design

**Status:** Spec, awaiting user review
**Author:** ocooke@waldorfwomenscare.com (with Claude)
**Date:** 2026-06-06

## Goal

Replace today's fine-grained `verb:resource` permission system with a coarser, ordinal **per-module tier** model that's easier to administer, easier to audit, and easier to extend when new modules are added.

The current system has ~60 permission strings sprinkled across `PERMISSIONS` and ~150 `require_permission(...)` call-sites. As modules are added the catalog grows unboundedly and assignment becomes unmanageable. Recent audits (smoke tests #43, #45) surfaced PHI-leak gaps that were direct consequences of this complexity — routers gated with `AUTH_ONLY` because no one knew which specific permission applied.

## Non-goals

- Row-level access control. "Work" tier sees all records in a module; the difference between Work and Manage is *what kinds of actions you can take*, not *which records you can see*. Row-level scoping (e.g., "biller only sees her assigned claims") is out of scope and can be added later as a UX-level default filter.
- Patient portal authentication. The patient portal uses signed token-based auth (`require_portal_token`) and is unaffected by this redesign.
- Webhook signature verification (Stripe, BoldSign). HMAC-signed endpoints are not permission-gated; the signature is the gate.
- Clinical role checks. `chart:sign` becomes a separate provider-role check, not a permission tier.

## Module catalog

15 modules, declared in `app/permissions/catalog.py`:

| # | Slug | Label | Manage = (in addition to Work) |
|---|---|---|---|
| 1 | `chart` | Chart | merge duplicate charts; configure problem-list templates; delete chart entries |
| 2 | `active_ar` | Active AR | bulk write-off; configure denial codes & workflow states; delete claims |
| 3 | `billing_bank_recon` | Billing – Bank Recon | configure recon rules; resolve overlap exceptions; delete reconciled records |
| 4 | `billing_missing_charges` | Billing – Missing Charges | issue provider portal tokens; bulk-mark complete; delete charges |
| 5 | `billing_insurance_docs` | Billing – Insurance Documents | hard-delete documents; bulk-assign; configure classifications |
| 6 | `billing_insurance_contacts` | Billing – Insurance Contacts | bulk import; delete contacts; configure source rules |
| 7 | `recall` | Recall | configure recall rules; bulk-schedule; delete recall lists |
| 8 | `surgery` | Surgery | configure block schedules / fee schedule / consent templates / surgery types; delete surgeries |
| 9 | `device_larc` | Device Tracking – LARC | bulk-import devices; configure inventory rules; delete devices |
| 10 | `device_office_procedures` | Device Tracking – Office Procedures | bulk-import devices; configure inventory rules; delete devices |
| 11 | `pellets` | Pellets | configure lots & dose schedules; configure Smartsheet sync; delete adjustments (within DEA constraints) |
| 12 | `reputation` | Reputation Management | configure review portal; configure response templates; delete reviews |
| 13 | `training` | Training | author training modules; assign training paths; mark complete on behalf of others |
| 14 | `my_checklist` | My Checklist | assign tasks to other users; configure recurring tasks |
| 15 | `audit_log` | Audit Log | export audit data; configure retention policy (rows remain append-only — no deletion ever) |

**Irregular semantics:**

- **My Checklist**: View/Work on one's own checklist is implicit for every authenticated user (nothing to grant). The tier on this module governs only *cross-user* access — seeing or assigning tasks to other people's checklists.
- **Audit Log**: View is the only ordinary grant. Work is no-op (system writes audit rows; staff don't). Manage exists for export/retention.
- **Reputation Management**: configure review portal — TBD if there are additional Manage actions; will refine after the first cutover.

## Tiers

Ordinal scale, declared in `app/permissions/catalog.py`:

```python
class Tier(IntEnum):
    NONE  = 0
    VIEW  = 10
    WORK  = 20
    MANAGE = 30
    ADMIN  = 40
    SUPER_ADMIN = 50
```

| Tier | Meaning |
|---|---|
| **View** | Read all records in the module (list + detail) |
| **Work** | View + add / edit individual records (the day-to-day) |
| **Manage** | Work + delete + configure module settings + bulk operations |
| **Admin** | *Per-module.* Manage + can grant View/Work/Manage on this module to other users |
| **Super Admin** | *Global.* Admin on every module + can grant the Admin tier to other users on any module |

**Strict containment**: a higher tier always includes all the powers of lower tiers on the same module.

**Per-module vs global**: View/Work/Manage/Admin are all per-module. Super Admin is a single global boolean on the user — there is no "Super Admin for module X."

**Granting Admin**: only Super Admin can grant the Admin tier. A per-module Admin can grant View/Work/Manage but cannot promote anyone to Admin (prevents privilege escalation chains).

**Last-Super-Admin safety**: the system enforces that at least one Super Admin always exists. Demoting yourself or another user is rejected if it would leave zero Super Admins.

## Resolution algorithm

Effective tier for `(user, module)`:

```
1. If user.is_super_admin:                       return SUPER_ADMIN
2. If override exists for (user, module):        return override.tier
3. Else if user belongs to any groups granting this module:
                                                 return max(group.tier for each group)
4. Else:                                         return NONE  (hidden)
```

Three lines. Override is a single tier value (including `NONE` to mean "denied"). Group memberships compose with `max` — there is never a "conflicting groups" failure mode.

## Group + override model

**Groups** hold default tier assignments. A group has zero or more `(module, tier)` rows in `group_module_tiers`.

**Per-user overrides** are stored in `user_module_overrides`. Each row is a single tier value for a `(user, module)` pair. The `NONE` value means "explicitly denied — ignore any group grants for this user on this module."

**Default Staff group**: a system-managed group that all auto-provisioned users are joined to on first sign-in. Initial baseline:

| Module | Tier |
|---|---|
| Chart | View |
| My Checklist | Work |

(My Checklist Work on the user's own checklist is implicit; this baseline grant just gives the new hire the "see/use my own checklist" UX without any administrator action.)

Administrators can edit the Default Staff group's tier map; future hires inherit the edited baseline.

## Cross-module actions

Actions that touch multiple modules (e.g., "fax a chart document") are gated by the **owning module** of the resource. Faxing a chart doc requires `Chart:Work`; faxing an insurance document requires `Insurance Documents:Work`. There is no separate Faxing module/tier.

This avoids the verb explosion that the current system has (`fax:read`, `fax:send`, ...) and keeps the rule consistent: if you can Work the source module, you can do the action.

## Data model

### New tables

```sql
CREATE TABLE group_module_tiers (
    group_id   text NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    module     text NOT NULL,           -- slug from Module enum
    tier       integer NOT NULL,        -- 0 (NONE) .. 40 (ADMIN)
    PRIMARY KEY (group_id, module)
);

CREATE TABLE user_module_overrides (
    user_email text NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    module     text NOT NULL,
    tier       integer NOT NULL,        -- 0 (NONE/denied) .. 40 (ADMIN)
    added_at   timestamp NOT NULL DEFAULT NOW(),
    added_by   text NOT NULL,
    PRIMARY KEY (user_email, module)
);
```

### New column on `users`

```sql
ALTER TABLE users ADD COLUMN is_super_admin boolean NOT NULL DEFAULT false;
```

### Dropped (in Phase 4)

- `group_permissions` table
- `users.permissions_extra` column
- `users.permissions_revoked` column
- `app/services/permissions.py` `PERMISSIONS` dict + helpers

## Code organization

### `app/permissions/catalog.py`

```python
from enum import Enum, IntEnum

class Module(str, Enum):
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
    NONE         = 0
    VIEW         = 10
    WORK         = 20
    MANAGE       = 30
    ADMIN        = 40
    SUPER_ADMIN  = 50

class ModuleSpec:
    label: str
    description: str
    manage_means: str

MODULE_REGISTRY: dict[Module, ModuleSpec] = { ... 15 entries ... }
```

### `app/permissions/resolver.py`

```python
def effective_tier(db, user_email: str, module: Module) -> Tier:
    """Resolve effective tier per the algorithm in §Resolution algorithm."""

def requires_tier(module: Module, min_tier: Tier) -> Callable:
    """FastAPI dependency factory. 403 if effective tier < min_tier."""
```

### Route gating pattern

```python
# Router-level baseline (View)
app.include_router(
    surgery.router, prefix="/api",
    dependencies=requires_tier(Module.SURGERY, Tier.VIEW),
)

# Per-endpoint elevation for writes
@router.patch("/{id}", dependencies=[requires_tier(Module.SURGERY, Tier.WORK)])
def edit_surgery(...): ...

@router.delete("/{id}", dependencies=[requires_tier(Module.SURGERY, Tier.MANAGE)])
def delete_surgery(...): ...
```

The router-level dependency replaces the current `dependencies=AUTH_ONLY` / `BILLING_READ` / etc. patterns in `main.py`.

## Admin UI

Per-user editor (`/admin/users/{email}`) and per-group editor (`/admin/groups/{id}`) share the same grid component.

**Per-user grid:**

```
Apetit Pettit                                       [Set as Super Admin]
apettit@waldorfwomenscare.com

Member of: Billing Coders, Front Desk      [Edit group membership]

┌─────────────────────────────────────────────────────────────────────────┐
│ Module                       View  Work  Manage  Admin  Denied   Source │
├─────────────────────────────────────────────────────────────────────────┤
│ Chart                         ●     o     o       o     o      Default  │
│                                                                Staff    │
│ Active AR                     o     o     ●       o     o      Override │
│ Billing - Bank Recon          o     ●     o       o     o      Billing  │
│                                                                Coders   │
│ Billing - Missing Charges     o     ●     o       o     o      Billing  │
│                                                                Coders   │
│ Surgery                       ●     o     o       o     o      Default  │
│                                                                Staff    │
│ ...                                                                     │
└─────────────────────────────────────────────────────────────────────────┘
                                                          [Save overrides]
```

Source column shows the **specific** group name (e.g., "Billing Coders", "Default Staff") rather than a generic label. This kills the "where does this permission come from?" question.

- `●` = effective tier · `o` = clickable to set as override · clicking the currently-active marker clears the override (falls back to group default)
- `Denied` is shown as a positive column so admins can deliberately deny a module that group membership would otherwise grant.

**Per-group grid:** same layout, but no Source column (it *is* the source) and no Override behavior (the cells are the group's defaults).

## Endpoints

```
GET    /api/admin/users/{email}/tiers          # resolved tier per module + source
PUT    /api/admin/users/{email}/overrides/{module}    # body: {"tier": "manage" | "denied" | null }
PUT    /api/admin/users/{email}/super_admin    # body: {"is_super_admin": true|false}
GET    /api/admin/groups/{group}/tiers
PUT    /api/admin/groups/{group}/tiers/{module}       # body: {"tier": "view" | "work" | "manage" | "admin" | null }
```

All write endpoints require `Tier.SUPER_ADMIN` (for `is_super_admin` toggle and Admin grants) or `Tier.ADMIN` on the module being granted (for View/Work/Manage grants).

## Audit

Every grant write goes through `log_action()`:

- `USER_GROUPS_UPDATED` — already exists; survives unchanged
- `USER_PERMS_OVERRIDE` — already exists; semantics shift from per-permission to per-module
- `GROUP_PERMS_UPDATED` — already exists; semantics shift from per-permission to per-module
- `SUPER_ADMIN_GRANTED` — new
- `SUPER_ADMIN_REVOKED` — new

Every audit row includes the actor (granter), the target (grantee user / group), the module, the before/after tier, and a free-form description.

## Migration plan

Four phases. The system is fully functional at every point; no flag-day.

### Phase 1 — Additive (new model ships alongside the old)

- Create tables `group_module_tiers`, `user_module_overrides`
- Add column `users.is_super_admin`
- Ship `app/permissions/catalog.py`, `app/permissions/resolver.py`
- Ship admin UI grid (read-only at first)
- No `require_permission(...)` sites change yet

### Phase 2 — Translate existing groups + auto-join Default Staff

- Author the canonical group → tier-map translation for the 8 existing groups
- Run a one-shot script that populates `group_module_tiers` based on each group's current `permissions_extra` membership
- Create the Default Staff group (Chart=View, My Checklist=Work)
- Auto-join every active user to Default Staff (idempotent; no-op for groups already containing them)
- Enable admin UI writes; admins can begin editing

At end of Phase 2, the new model is fully populated. No route is using it yet.

### Phase 3 — Per-module cutover

One PR per module. Suggested order (lowest blast radius first):

1. Audit Log
2. Reputation Management
3. Training
4. My Checklist
5. Recall
6. Bank Recon
7. Missing Charges
8. Insurance Contacts
9. Insurance Documents
10. Device Tracking – LARC
11. Device Tracking – Office Procedures
12. Pellets
13. Chart
14. Surgery
15. Active AR

Each PR:
- Replaces the router-level `Depends(require_permission(...))` with `requires_tier(Module.X, Tier.VIEW)`
- Adds per-endpoint `requires_tier(Module.X, Tier.WORK)` / `Tier.MANAGE` decorators on the relevant routes
- Removes the now-unused `require_permission(...)` calls from that module's routers
- Smoke tests the module post-deploy (the same pattern as smoke tests #43–48)

### Phase 4 — Cleanup

After all 15 modules cut over:
- Delete `app/services/permissions.py` `PERMISSIONS` dict and `effective_permissions()` helper
- Drop tables/columns: `group_permissions`, `users.permissions_extra`, `users.permissions_revoked`
- Delete the legacy `require_permission()` helper

### Translation cheat-sheet (Phase 2)

| Old permission | New tier |
|---|---|
| `patient:read` | `Chart:View` |
| `patient:create`, `patient:edit` | `Chart:Work` |
| `chart:read`, `chart:edit` | `Chart:View`, `Chart:Work` |
| `chart:sign` | `Chart:Work` + provider-role check at endpoint |
| `document:read` / `upload` / `delete` (on `documents.router`) | `Chart:View` / `Chart:Work` / `Chart:Manage` |
| `document:read` etc. (on `billing_documents.router`) | `Insurance Documents:View` / `Work` / `Manage` |
| `intake:read`, `intake:edit` | `Chart:View`, `Chart:Work` |
| `fax:read`, `fax:send` | collapsed — gated by owning module |
| `claim:read` | `Active AR:View` |
| `claim:edit` | `Active AR:Work` |
| `claim:writeoff` | `Active AR:Manage` |
| `payment:post` | `Active AR:Work` |
| `bankrecon:read` | `Bank Recon:View` |
| `audit:read` | `Audit Log:View` |
| `report:financial` | `Active AR:Manage` |
| `surgery:work` | `Surgery:Work` |
| `larc:read`, `larc:edit`, etc. | `Device Tracking - LARC:*` |
| `pellet:*` | `Pellets:*` |
| `user:manage` | `Admin` (cross-module — see §Tiers) |

## Open questions

- Reputation Management Manage scope — confirm with first cutover whether "configure review portal" is the right boundary or if there are admin-flavored actions to add.
- Office Procedures vs LARC parity — confirm both modules share the exact same tier map definition.
- Dashboard split — if any non-billing dashboard appears later (e.g., clinical operations dashboard), revisit whether `report:financial` → `Active AR:Manage` is still the right mapping or if a dedicated Reports module is warranted.

## Future work (out of scope)

- Row-level scoping ("Work users only see their own assignments")
- Time-bounded grants ("View access expires after 30 days")
- Self-service request workflow ("user requests Manage on Recall; Admin approves")
- Cross-account inheritance for caribcall.com users vs waldorfwomenscare.com users
