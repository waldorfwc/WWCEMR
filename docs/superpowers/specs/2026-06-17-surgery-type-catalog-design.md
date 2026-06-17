# Surgery Type Catalog Design

**Status:** Approved 2026-06-17. Replace the hardcoded surgery-procedure dropdown with a DB-backed,
fully-editable **Surgery Type catalog**: each type maps to one or more CPTs, carries a
minor/major/office classification, may be restricted to certain locations, and explicitly references
the consent template(s) that apply. Selecting a type during intake auto-fills the surgery.

## Goal
The "Surgery Name" dropdown in surgery intake is populated from a hardcoded `PROCEDURES` list in
`backend/app/services/surgery/picklists.py`. Staff cannot add or edit surgery types. Make the entire
list a managed catalog: staff add/edit/remove types, each type maps name → CPT(s) → consent
template(s), and picking a type pre-fills the surgery's procedures, locations, classification, and
consent selection.

## Decisions (from brainstorming)
- **Catalog replaces the hardcoded list.** Migrate today's ~30 `PROCEDURES` into a DB table seeded
  on first run; after seeding the whole list is editable. Nothing stays hardcoded.
- **Fields:** name + one-or-more CPTs + classification (minor/major/office). A type may also carry
  eligible locations and explicit consent template references.
- **Consent is EXPLICIT, on the surgery type.** Each type references which `ConsentTemplate`(s)
  apply (by ID). Selecting a type pre-fills the surgery's `consent_template_ids`. The existing
  CPT/keyword `ConsentTemplate` matcher remains the fallback for surgeries created without a catalog
  type — so nothing regresses. We do NOT edit each `ConsentTemplate`'s CPT list from here.
- **Fee schedule stays CPT-derived.** The type's CPTs drive fees downstream exactly as today; no
  fee mapping is added to the catalog.
- **Intake auto-fills.** Selecting a type fills the surgery's procedures with all its CPT(s),
  pre-selects its eligible locations, sets the classification, and pre-fills consent templates — all
  still editable by the coordinator.

## Data model — new `SurgeryType` (`backend/app/models/surgery_type.py`)
```
class SurgeryType(Base):
    __tablename__ = "surgery_types"
    id                   = Column(GUID(), primary_key=True, default=new_uuid)
    name                 = Column(String(200), nullable=False)        # the dropdown label
    cpts                 = Column(JSON, nullable=False, default=list) # [{"cpt": "58558", "description": "..."}]
    classification       = Column(String(20), nullable=False, default="minor")  # minor|major|office
    eligible_facilities  = Column(JSON, nullable=False, default=list) # subset of [medstar, crmc, office]; [] = all
    consent_template_ids = Column(JSON, nullable=False, default=list) # explicit ConsentTemplate IDs
    active               = Column(Boolean, nullable=False, default=True)
    sort_order           = Column(Integer, nullable=False, default=0)
    created_at           = Column(DateTime, default=now_utc_naive, nullable=False)
    updated_at           = Column(DateTime, default=now_utc_naive, onupdate=now_utc_naive, nullable=False)
```
- Register in `app/database.py` `init_db()` (`from app.models import surgery_type as _surgery_type`)
  so the table auto-creates (lightweight-migration pattern — no Alembic).
- `classification` valid values: `minor`, `major`, `office`. `eligible_facilities` values must be a
  subset of `SURGERY_FACILITY_VALUES` (`medstar`, `crmc`, `office`, `wwc_office_white_plains`);
  `[]` means all.

## Seed (`backend/app/services/surgery/surgery_type_seed.py`)
- On startup (called from `init_db()` after table creation, idempotent: only seed when the table is
  empty), insert one `SurgeryType` per entry in the existing `picklists.PROCEDURES`:
  - `name` = the procedure `description`; `cpts` = `[{"cpt", "description"}]` (single-CPT to start).
  - `classification`: `major` if CPT ∈ `MAJOR_CPTS` (in `smartsheet_seed`/`order_parser`), else
    `minor`. There is no office-CPT set, so the seed never assigns `office`; staff set `office`
    manually on the few in-office types after seeding.
  - `eligible_facilities` = `[]` (all); `consent_template_ids` = `[]`; `active` = True;
    `sort_order` = list index.
- After seeding, `picklists.PROCEDURES` is retained ONLY as the seed source + back-compat constant;
  the live dropdown reads the DB catalog.

## Backend — service (`backend/app/services/surgery/surgery_types.py`)
- `list_types(db, *, include_inactive=False) -> list[SurgeryType]` ordered by `sort_order, name`.
- `create_type(db, payload) / update_type(db, id, payload) / set_active(db, id, active) /
  reorder(db, ordered_ids)`.
- `as_picklist(types) -> list[dict]`: `{id, name, cpts, classification, eligible_facilities,
  consent_template_ids}`.
- Validation: `name` non-empty; `cpts` non-empty, each `{cpt (non-empty), description}`;
  `classification` ∈ the three values; `eligible_facilities` ⊆ `SURGERY_FACILITY_VALUES`;
  `consent_template_ids` reference existing `ConsentTemplate` rows (drop unknown IDs).

## Backend — router (`backend/app/routers/surgery.py`)
- Modify `GET /surgery/picklists` (currently `all_picklists()` at ~line 1544, `Tier.VIEW`) to include
  `surgery_types` from the catalog. Keep `procedures` as a flattened `[{cpt, description}]` built
  from the active catalog's CPTs (back-compat for any other consumer).
- New CRUD endpoints (`Tier.MANAGE`), prefix consistent with existing surgery admin routes:
  - `GET    /surgery/admin/surgery-types?include_inactive=` → list (full objects).
  - `POST   /surgery/admin/surgery-types` → create.
  - `PUT    /surgery/admin/surgery-types/{id}` → update.
  - `DELETE /surgery/admin/surgery-types/{id}` → soft-delete (set `active=False`).
  - `POST   /surgery/admin/surgery-types/reorder` → body `{ordered_ids: [...]}`.
- Pydantic `SurgeryTypePayload`: `name`, `cpts: list[{cpt, description}]`, `classification`,
  `eligible_facilities: list[str]`, `consent_template_ids: list[str]`, `active`, `sort_order`.

## Frontend — intake (`frontend/src/components/surgery/SurgeryIntakeForm.jsx`)
- `useQuery(['surgery-picklists'])` already runs. Read `picks?.surgery_types` for the "Surgery Name"
  dropdown (label = `name`). On select, set form state:
  - `procedures` = the type's `cpts` (one row per CPT, `{cpt, description}`).
  - `eligible_facilities` = the type's `eligible_facilities` (pre-select; coordinator can change).
  - `procedure_classification` = the type's `classification`.
  - `consent_template_ids` = the type's `consent_template_ids` (pre-fill the curated consent set).
  - All fields remain editable after auto-fill. If `surgery_types` is empty/missing, fall back to the
    flattened `procedures` list (no crash).

## Frontend — staff management (`frontend/src/.../SurgerySettings*`)
- Add a **"Surgery Types"** section/tab to Surgery Settings: a table of types
  (name, CPT count, classification, locations, active) with add / edit / remove / reorder.
- Editor row/modal fields: name; CPT rows (add/remove `{cpt, description}`); classification select
  (Minor / Major / Office); eligible-locations multi-select (MedStar / CRMC / Office; none = all);
  consent-template multi-select (from the existing consent-templates list endpoint); active toggle.
- Wire to the new `/surgery/admin/surgery-types` endpoints via the staff `api` + react-query;
  invalidate `['surgery-picklists']` on save so intake reflects changes immediately.

## Testing
- **Backend (TDD):**
  - Seed runs once and is idempotent; seeded count == `len(PROCEDURES)`; classification mapping
    correct for a known major CPT, a known office CPT, and a default-minor CPT.
  - CRUD: create (validates name/cpts/classification), update, soft-delete (`active=False`,
    excluded from default list, included with `include_inactive`), reorder.
  - `consent_template_ids` validation drops IDs that don't reference a real `ConsentTemplate`.
  - `GET /surgery/picklists` returns `surgery_types` (full objects) AND a flattened `procedures`.
  - MANAGE tier enforced on the admin endpoints; VIEW can read picklists.
- **Frontend:** build clean; intake auto-fill sets procedures/facilities/classification/consent from
  a selected type (headless render optional).
- **Authenticated walk-through** (`backend/tests/test_surgery_type_catalog_walkthrough.py`): staff
  creates a new surgery type (name + 2 CPTs + major + MedStar + a consent template) → it appears in
  `GET /surgery/picklists` `surgery_types` → a seeded built-in type is editable and soft-deletable →
  edits are reflected in the picklist.

## Out of scope (YAGNI)
- No fee-schedule mapping in the catalog (CPT-derived). No editing of `ConsentTemplate` CPT lists from
  the catalog (consent is referenced by ID). No change to how consent/fees are computed downstream.
  Existing surgeries are untouched (they already store their own `procedures` / `consent_template_ids`).
  No bulk import; staff edit types individually.

## Conventions
MM/DD/YYYY dates, Title Case section titles/headers/buttons, money `$X.XX`; no secrets in source
(env + Secret Manager); lightweight migrations via `init_db()` model registration (no Alembic);
`now_utc_naive()` never `datetime.utcnow()`; deploy with `--project=wwc-solutions` and `--tag=`.
