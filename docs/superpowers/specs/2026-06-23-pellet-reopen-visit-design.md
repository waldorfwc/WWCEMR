# Reopen Pellet Visit (+ Missing-Lot Flag) — Design

**Date:** 2026-06-23
**Module:** Pellet
**Status:** Approved (brainstorm complete)

## Goal

Let a manager **reopen** a completed (`inserted` / `billed`) or `cancelled` pellet
visit to correct it — bind/fix lot numbers, change dose quantity/type, add/remove
doses, fix visit fields — while keeping pellet inventory accurate and recording a
full audit trail. Plus a **missing-lot flag** so staff can find the visits that need
fixing instead of discovering them one at a time.

## Background

Investigation of the pellet module (2026-06-23) established:

- A visit's lot lives on child `PelletVisitDose.lot_id` (model `backend/app/models/pellet.py`),
  which is **nullable**, and **nothing enforces** a lot before a visit reaches
  `inserted` / `billed`.
- `PelletVisit.status` ∈ `new | in_progress | inserted | billed | cancelled | rescheduled`.
- Lots are bound (and stock decremented) at fill-bag / confirm-as-planned /
  confirm-insertion. Stock consumption == binding a `lot_id` to a dose; there is no
  separate ledger row.
- A visit can be `inserted`/`billed` with **no lot** via: ModMed appointment import
  (creates the visit `inserted` with **zero dose rows**); confirm without a fill-bag
  step (dose stays `lot_id = NULL`); historical backfill (`is_historical=True`);
  Smartsheet history rows with a blank Lot # column.
- Existing manager-edit of confirmed visits is **stock-neutral** (e.g. `append_dose`
  branches on `is_confirmed_visit`). There is **no reopen action** today and **no
  query/sweep** that flags visits missing a lot.

This is the concrete trigger: chart 14943 (Catrina Tober), 6/5/26 visit, no lot.

## Decisions (from brainstorm)

1. **Reopenable states:** `inserted`, `billed`, and `cancelled`.
2. **Inventory on edit:** *same rules as the live flow* — keyed on `is_historical`,
   not status. Real visits move stock; historical backfills stay stock-neutral.
3. **Permission:** managers + super-admin (pellet **MANAGE** tier).
4. **Billing:** a reopened `billed` visit **stays billed** on close; every reopen /
   close writes an audit entry. No re-bill flag.

## Scope

### In scope
- Reopen / close-reopen actions + the four tracking columns.
- The `is_historical` stock guard so reopened editing reconciles inventory correctly.
- Backend endpoints + frontend reopen button, reason prompt, "reopened" banner, and
  close button.
- A missing-lot flag: a query helper, a dashboard counter + list filter, and a
  per-visit warning badge.
- Pellet manual section.

### Out of scope (YAGNI)
- Re-bill workflow / charge recalculation (billed stays billed).
- Changing how the *live* insertion flow works.
- Backfilling lots automatically / bulk-fix tooling (manager fixes each via reopen).
- A hard non-null DB constraint on `lot_id` (would break historical/import rows).

## Architecture

### State model

Add to `PelletVisit` (lightweight migration, matching the repo's
`_apply_lightweight_migrations` pattern):

| Column | Type | Notes |
|---|---|---|
| `reopened_at` | DateTime, nullable | set on reopen, cleared on close |
| `reopened_by` | String(120), nullable | actor email |
| `reopened_reason` | Text, nullable | required free-text reason |
| `pre_reopen_status` | String(20), nullable | the status we reopened *from* |

"Is currently reopened" == `reopened_at is not None`.

**Reopen** (`reopen_visit(db, visit, *, by, reason)`):
- Allowed only when `status ∈ {inserted, billed, cancelled}` **and** not already
  reopened (`reopened_at is None`); else 409.
- `reason` required (non-empty) else 422.
- Sets `pre_reopen_status = status`, stamps `reopened_at/by/reason`, flips
  `status → "in_progress"` so the existing edit endpoints operate normally.
- Writes a pellet audit entry: action `visit_reopened`, detail `{from: <pre_status>, reason}`.

**Edit while reopened:** uses the existing in-progress edit endpoints (doses + visit
fields). No new edit endpoints. The only behavioral change is the stock guard below.

**Close reopen** (`close_reopen(db, visit, *, by)`):
- Allowed only when currently reopened (`reopened_at is not None`); else 409.
- Target status: **`billed` if `pre_reopen_status == "billed"`, otherwise `inserted`.**
  (So billed stays billed; a visit reopened from `cancelled` is treated as
  un-cancelled and completes to `inserted`; reopened-from-`inserted` returns to
  `inserted`.) If the manager reopened a cancelled visit in error, they use the
  normal cancel action to re-cancel.
- Sets `status` to the target, clears `reopened_at/by/reason` and `pre_reopen_status`.
- Writes a pellet audit entry: action `visit_reopen_closed`, detail `{to: <target>}`.

### Inventory reconciliation — "same rules as live flow"

**The load-bearing rule: dose-edit stock movement keys on `is_historical`, not status.**

- **Real visit** (`is_historical=False`): once reopened (now `in_progress`), the
  existing edit logic treats lot binding as a live pull → decrements that lot;
  removing / swapping a lot returns stock. So binding the missing lot **corrects**
  the previously-overstated count, and adding a dose to a zero-dose import visit pulls
  real stock. This is exactly the live behavior — no new stock code.
- **Historical backfill** (`is_historical=True`): editing must stay **stock-neutral**
  even while `status == "in_progress"`. Today the neutral path is selected by
  `is_confirmed_visit` (status inserted/billed); reopening flips status to in_progress,
  which would wrongly enable stock movement. **Fix:** the dose-edit stock path must
  short-circuit to stock-neutral whenever `visit.is_historical` is true, independent of
  status. This is the single new guard in the live edit code.

Implementation note for the plan: find every place a dose edit adjusts stock (fill-bag,
confirm-as-planned, append_dose, confirm-insertion swaps/additions) and ensure the
"skip stock" condition is `is_historical OR <existing confirmed checks>`. The cleanest
form is a small helper `_visit_is_stock_neutral(visit) -> bool` returning
`visit.is_historical` (plus any existing confirmed-visit condition) used at each site,
so the rule lives in one place.

### Missing-lot flag

A visit "needs a lot" when it is a real, completed visit that lacks lot data:

```
status in ("inserted", "billed")
AND is_historical = False
AND (
    visit has zero PelletVisitDose rows
    OR any PelletVisitDose has lot_id IS NULL
)
```

(Historical backfills are excluded — they are knowingly incomplete and not a data
error.)

- **Query helper** `visits_missing_lot(db) -> list[PelletVisit]` (and a count variant)
  in the pellet service.
- **Dashboard counter + filter:** the pellet dashboard gains a "Missing Lot: N" stat;
  clicking it filters the visit list to those visits. Implemented as a new filter value
  on the existing visit-list endpoint (e.g. `?flag=missing_lot`) so it reuses the list
  UI.
- **Per-visit badge:** the visit detail (and list row) shows a small amber "Missing
  lot" warning when the visit matches, with a hint to reopen + fix.

## Components

- **Backend**
  - Migration: 4 columns on `pellet_visits`.
  - Service (`backend/app/services/pellet/...`): `reopen_visit`, `close_reopen`,
    `_visit_is_stock_neutral`, `visits_missing_lot` (+ count).
  - Stock guard wired into the existing dose-edit sites.
  - Router (`backend/app/routers/pellet.py`): `POST /pellet/visits/{id}/reopen`
    (MANAGE, body `{reason}`), `POST /pellet/visits/{id}/close-reopen` (MANAGE); a
    `flag=missing_lot` option on the visit-list endpoint; the dashboard count field.
  - Audit entries via the existing pellet audit log.
- **Frontend** (pellet visit detail + dashboard/list pages)
  - "Reopen Visit" button (shown for inserted/billed/cancelled to MANAGE users) opening
    a reason prompt → `POST .../reopen` → refetch.
  - A "Reopened by X — editing enabled" banner while reopened, with a "Done Editing"
    button → `POST .../close-reopen` → refetch.
  - "Missing Lot: N" dashboard stat → filters the list; amber badge on matching visits.
  - Titles/buttons in Title Case; dates MM/DD/YYYY.
- **Docs:** pellet manual section "Reopening & Correcting a Past Visit" (and a note on
  the missing-lot flag).

## Data flow

```
Reopen:   detail → POST /pellet/visits/{id}/reopen {reason}
          → reopen_visit: guard state, save pre_reopen_status, status→in_progress, audit
Edit:     existing in-progress dose/visit endpoints
          → stock moves iff NOT is_historical (live rules); historical = neutral
Close:    banner → POST /pellet/visits/{id}/close-reopen
          → close_reopen: status→ (billed if pre=billed else inserted), clear flags, audit
Flag:     dashboard count + ?flag=missing_lot list filter ← visits_missing_lot(db)
```

## Error handling

| Case | Behavior |
|---|---|
| Reopen when status ∉ {inserted, billed, cancelled} | 409 |
| Reopen when already reopened (`reopened_at` set) | 409 |
| Reopen with empty reason | 422 |
| Close when not reopened | 409 |
| Non-MANAGE user calls reopen/close | 403 (tier dependency) |
| Editing a reopened historical visit | allowed, stock-neutral (guard) |

## Testing

Backend (pytest):
- `reopen_visit`: from inserted → status `in_progress`, `pre_reopen_status="inserted"`,
  stamps set; from billed likewise; from cancelled likewise; 409 from `new`/`in_progress`;
  409 if already reopened; 422 empty reason; audit row written.
- `close_reopen`: pre=billed → returns `billed`; pre=inserted → `inserted`;
  pre=cancelled → `inserted`; clears flags; 409 when not reopened; audit row.
- **Inventory:** reopen a real inserted visit with a dose `lot_id=NULL`, bind a lot →
  that lot's stock decrements by the qty; reopen + add a dose to a zero-dose real visit
  → stock decrements; reopen a historical visit + edit → **stock unchanged**.
- `visits_missing_lot`: returns real inserted/billed visits with zero doses or a
  null-lot dose; excludes historical; excludes fully-lotted visits; excludes
  new/in_progress.
- Endpoint tier gating (403 for non-MANAGE); `?flag=missing_lot` returns the right set.

Frontend: build green; reopen button visible only for eligible states + MANAGE; banner
+ close button appear while reopened; missing-lot badge renders for matching visits.

## Files (anticipated)

- Modify: `backend/app/models/pellet.py` (4 columns)
- Modify: `backend/app/database.py` or the lightweight-migration module (add columns)
- Modify/Create: `backend/app/services/pellet/*.py` (reopen/close/stock-guard/missing-lot)
- Modify: `backend/app/routers/pellet.py` (endpoints + list filter + dashboard count)
- Test: `backend/tests/test_pellet_reopen_visit.py` (new)
- Modify: pellet visit-detail + dashboard/list frontend components
- Modify: `backend/app/services/manual_seed.py` (pellet manual section)
