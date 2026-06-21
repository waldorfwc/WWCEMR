# Consolidated Module Manuals — Design

**Date:** 2026-06-21
**Status:** Approved (design); spec under review

## Problem

Two modules (LARC, Pellets) ship an in-app operating manual. They are
copy-paste twins: each has its own table (`larc_manual_sections`,
`pellet_manual_sections`), its own `/manual` CRUD router, its own seed list,
and its own near-identical React page (`LarcManual.jsx`, `PelletManual.jsx`).
Every other module — Surgery, Active AR, Billing, Charts, Recalls, Marketing,
Training — has **no** manual. There is also no signal when a manual has gone
stale relative to the module it documents.

## Goals

1. **Consolidate** the duplicated manual machinery into **one reusable,
   module-keyed manual system** (model, API, seed registry, React component).
2. **Migrate** the existing LARC + Pellet manuals onto it **without losing any
   in-app edits** the practice has made.
3. **Author manuals** for the major modules that lack one.
4. **Keep manuals current** as modules change, via a staleness badge plus a
   standing convention.

## Non-goals

- Manuals for thin utility areas this pass: **Insurance Contacts**, **Audit
  Log**, **My Checklist**. The framework makes adding them trivial later.
- Dropping the old `*_manual_sections` tables. They are left in place as a
  read-only backup after migration; the new system never reads them again.
- Versioning / revision history of manual edits (out of scope).

## Architecture

### Data model — one table

New `manual_sections` table (replaces the two per-module tables):

| Column | Type | Notes |
|---|---|---|
| `id` | GUID PK | |
| `module` | String(40), not null | module key, e.g. `"surgery"`, `"device_larc"` (matches `Module` enum string) |
| `slug` | String(80), not null | TOC anchor, unique **per module** |
| `title` | String(200), not null | |
| `body_md` | Text, not null, default `""` | markdown |
| `sort_order` | Integer, not null, default 0 | |
| `created_at` | DateTime, default now | |
| `updated_at` | DateTime, default now, onupdate now | drives the staleness badge |
| `updated_by` | String(200), nullable | user email or `system:seed` |

- Uniqueness: `UniqueConstraint(module, slug)` + index on `module`.
- `init_db()` auto-creates the table via `Base.metadata.create_all` (no manual DDL).

### Migration — preserve edits

A one-shot idempotent migration runs in `init_db()` after the table exists
(alongside the existing lightweight migrations):

```
for (old_table, module) in [(larc_manual_sections, "device_larc"),
                            (pellet_manual_sections, "pellets")]:
    for row in old_table:
        if not exists manual_sections(module=module, slug=row.slug):
            insert manual_sections(module=module, slug=row.slug, title=row.title,
                                   body_md=row.body_md, sort_order=row.sort_order,
                                   created_at=row.created_at, updated_at=row.updated_at,
                                   updated_by=row.updated_by)
```

- **Edit-safe:** copies the *current* row (including practice edits), and only
  when the `(module, slug)` target doesn't already exist — so re-running never
  clobbers anything.
- Guarded so it's skipped cleanly if an old table is absent (fresh installs).
- The old tables are **not** dropped — they remain as a backup.

### Seed registry — one place

A single registry maps module → default sections:

```python
MANUAL_SEEDS: dict[str, list[tuple[str, str, int, str]]] = {
    "device_larc": LARC_MANUAL_SECTIONS,     # moved from larc/seed.py (incl. the
                                             # checkout-quick-action + device-ownership
                                             # sections added 2026-06-21)
    "pellets":     PELLET_MANUAL_SECTIONS,   # moved from pellet/seed.py
    "surgery":     SURGERY_MANUAL_SECTIONS,
    "active_ar":   ACTIVE_AR_MANUAL_SECTIONS,
    "billing_bank_recon":     BANK_RECON_MANUAL_SECTIONS,
    "billing_missing_charges": MISSING_CHARGES_MANUAL_SECTIONS,
    "billing_insurance_docs":  INSURANCE_DOCS_MANUAL_SECTIONS,
    "chart":       CHART_MANUAL_SECTIONS,
    "recall":      RECALL_MANUAL_SECTIONS,
    "reputation":  REPUTATION_MANUAL_SECTIONS,
    "training":    TRAINING_MANUAL_SECTIONS,
}
```

One idempotent `seed_manuals()` loops every module and inserts only
`(module, slug)` rows that don't already exist — same "only add missing" rule
the current seeds use, so existing/edited sections are never overwritten. The
per-module LARC and Pellet seed functions are removed; their content moves into
the registry verbatim (LARC carries the two sections seeded earlier today).

### API — one router

New `app/routers/manual.py`, mounted at `/api/manual`:

| Method | Path | Tier | Behavior |
|---|---|---|---|
| GET | `/manual?module=X` | module X **VIEW** | list sections for X, ordered by `sort_order, title` |
| POST | `/manual` (body has `module`) | module X **MANAGE** | create section |
| PATCH | `/manual/{id}` | section's module **MANAGE** | update title/body/sort_order, stamp `updated_at`/`updated_by` |
| DELETE | `/manual/{id}` | section's module **MANAGE** | delete |

- A `MODULE_BY_KEY` map resolves the `module` string → `Module` enum for the
  `requires_tier(...)` check. Unknown/again-missing module → 400.
- `requires_tier` can't be parameterized by a request value at decorate time,
  so these handlers take `current_user` via a light auth dependency and call an
  explicit `assert_tier(current_user, module, tier)` helper inside the body
  (the project already resolves tiers from the DB, not JWT claims).
- The old `/larc/manual` and `/pellets/manual` routers are **removed** (only the
  manual pages consumed them).

### Frontend — one component

New `frontend/src/components/manual/ModuleManual.jsx` — generalized from
`LarcManual.jsx` (TOC, `marked` + `DOMPurify` render, in-app section editing,
add/delete for MANAGE). Props: `module` (key), `title` (heading), `blurb`
(sub-heading line), `backTo` (breadcrumb path).

- Calls `GET /manual?module={module}`; mutations POST/PATCH/DELETE with the
  module key; query key `['manual', module]`.
- `LarcManual.jsx` and `PelletManual.jsx` are **deleted**; their routes render
  `<ModuleManual module="device_larc" .../>` / `module="pellets"`.
- Every other module gets a **Manual** nav link + a `manual` route rendering
  `<ModuleManual module="..." .../>`. For modules whose nav is a flat page
  rather than a tabbed `*Nav` (e.g. Charts, Bank Recon), the Manual link is
  added in that area's existing nav/menu; exact placement per module noted in
  the plan.

### Staleness badge

- Each section already has `updated_at`. The manual page shows
  **"Updated MM/DD/YYYY"** per section (using `fmt.date`).
- When `now - updated_at > MANUAL_STALE_AFTER_DAYS`, show a small amber
  **"Review"** badge on the section header and in the TOC entry.
- `MANUAL_STALE_AFTER_DAYS = 180` is a single module-level constant in the
  shared manual code. **Open simplification for review:** the design earlier
  said "configurable in settings"; to avoid building a settings surface for one
  number, this spec makes it a constant (trivially changeable, promotable to a
  per-practice setting later). Flag if you want the settings row now.
- "Reviewed" is defined as "edited/saved" — saving a section (even with no text
  change is not forced; any PATCH) updates `updated_at`. No separate
  reviewed-at field this pass.

### Convention — keep manuals current

- Save an auto-memory (`feedback`): **when a module's behavior changes, update
  that module's manual section(s) in the same change.** This makes the
  manual-update step part of future feature work automatically.
- Record the same rule in this spec and in the manual's own "house style" note
  so the team follows it for manual edits made outside code.

## Content authoring

Each new module's seed sections are authored in the **established house style**
— short, operational, task-oriented markdown per workflow stage, with tables,
numbered steps, and `>` callouts (see LARC/Pellet sections as the template).

Primary source material already exists: **`frontend/src/components/help/helpContent.jsx`**
carries detailed per-page help bodies for these modules, plus each module's
pages/routers. Authoring = distill that into manual sections; do **not** invent
behavior — describe what the code/help actually does.

Modules + the sections each manual should cover (starter set; refine while
authoring):

- **Surgery** — overview; intake/new surgery; benefits & patient
  responsibility; consent (BoldSign); scheduling & block calendar; pre-op/
  post-op steps; statuses & auto-transitions; billing close-out; settings.
- **Active AR / Claims / Denials / Appeals** — overview & data import; claim
  queue & statuses; payments/ERA posting; denials workflow; appeals; Active AR
  views & filter presets.
- **Billing — Bank Recon** — overview; reconciliation workflow; matching;
  month close.
- **Missing Charges** — overview; charge-capture review; resolution.
- **Insurance Documents** — overview; uploading/classifying; retrieval.
- **Patient Charts & Documents** — overview; chart lookup; documents; faxing;
  recalls linkage.
- **Recalls** — overview; recall lists; outreach; pellet-recall reuse note.
- **Marketing / Reputation** — overview; reviews; leaderboard; profiles.
- **Training** — overview; training cards; completion tracking.

## Build order (informs the plan)

1. **Framework + migration** — model, migration, unified router, seed registry,
   shared `ModuleManual` component; migrate LARC + Pellet (delete old pages/
   routers/seed fns). LARC/Pellet manuals must keep working throughout.
2. **Staleness badge** — per-section "Updated" + stale "Review" badge.
3. **Per-module content** — one task per module (Surgery, Active AR, Bank
   Recon, Missing Charges, Insurance Docs, Chart, Recall, Reputation, Training):
   author seed sections + add the Manual nav link + route.
4. **Convention** — save the memory; add the house-style note.

## Testing

Backend (pytest):
- **Migration is edit-safe** — seed a `larc_manual_sections` row with an edited
  body, run the migration, assert it appears in `manual_sections` with
  `module="device_larc"` and the edited body; run again, assert no duplicate and
  the body is unchanged.
- **Seed is additive-only** — pre-insert a `(module, slug)` with a custom body,
  run `seed_manuals()`, assert the custom body survives and other slugs are added.
- **API tier scoping** — GET `/manual?module=surgery` requires Surgery VIEW;
  PATCH requires the section's module MANAGE; cross-module/unknown module → 403/400.
- **Per-module seed presence** — each module key in `MANUAL_SEEDS` yields ≥1
  section after seeding.

Frontend: `npm run build` (no JS test runner). Manual visual check of one
migrated manual (LARC) + one new manual (Surgery) renders with TOC + badge.

## Risks

- **Migration data loss** — mitigated by copy-only-if-absent + leaving old
  tables intact as backup; covered by the edit-safe test.
- **Tier resolution in the unified router** — `requires_tier` is route-time;
  the per-request `assert_tier` helper must mirror its DB-based logic exactly.
  Covered by the API tier-scoping tests.
- **Content accuracy** — manuals must describe real behavior; sourcing from
  `helpContent.jsx` + code (not invention) is the guardrail, with a spec-
  compliance review per module.
- **Scope size** — the content pass is large; it is decomposed one module per
  task so each is independently reviewable and the framework ships first.
