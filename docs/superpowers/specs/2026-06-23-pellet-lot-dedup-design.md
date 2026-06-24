# Pellet Lot Dedup — Design

**Date:** 2026-06-23
**Module:** Pellet (DEA Schedule III inventory)
**Status:** Approved (brainstorm complete)

## Goal

End duplicate pellet lots: **prevent** new ones at receiving, and **merge** the
existing duplicates — keyed per office — preserving every stock total and
chain-of-custody link, with a full audit trail.

## Background

`PelletLot` has no uniqueness on `(qualgen_lot_number, dose_type)`. `create_receipt`
(`pellet.py:1557`) mints a new lot **unconditionally** per receipt, and the
Smartsheet history import auto-creates lots when a number isn't found
(`pellet_smartsheet_history_import.py:354,495`). Result (measured 2026-06-23, read-only):
**75 lots → 15 duplicate groups → 32 redundant records (~43%)**. One physical batch
fragments across many records (e.g. L070 38mg = 9, L034 100mg = 6, L015 13mg = 5),
splitting its doses, stock, and history. Typically each group = one old Smartsheet
record (bulk of historical dose refs, 0 on-hand, placeholder expiration 2099, no
receipt) + recent receipt records (real stock + expiration).

Six FK columns reference `pellet_lots.id` and must be re-pointed by any merge:
`PelletStock.lot_id`, `PelletVisitDose.lot_id`, `PelletAuditEvent.lot_id`,
`PelletTransfer.lot_id`, `PelletDisposal.lot_id`, `PelletCountLine.lot_id`.
`PelletStock` has `UniqueConstraint(lot_id, location)`. `_get_or_create_stock`,
`_adjust_stock`, and `_audit` are the existing primitives.

## Decisions (from brainstorm)

1. **Model B — a lot record is per office.** Merge key = `(qualgen_lot_number,
   dose_type_id, office)`. The same Qualgen lot at White Plains and Brandywine stays
   as two records.
2. **Add a `location` column to `PelletLot`** so a lot's office is an explicit field
   (not derived), enabling DB-level enforcement.
3. **Delete** duplicate lot rows after re-pointing (not mark-inactive) — all their
   references move to the canonical, so the rows carry no remaining data.

## Architecture

Two independent pieces + an ordering:

- **Prevent** (code, ships first): `create_receipt` upserts on the merge key; a partial
  unique index makes recurrence impossible.
- **Clean up** (one-time migration): merge existing duplicates, audited, idempotent,
  dry-run first, totals-preserved.
- **Sequence:** prevent → dry-run merge → apply merge → physical count.

### New column + index

- `PelletLot.location` — `String(40)`, nullable (added via the lightweight-migration
  `needed` list). Backfilled by the merge migration (and set on every new lot by the
  prevent code).
- **Partial unique index** `uq_pellet_lot_number_dose_loc` on
  `(qualgen_lot_number, dose_type_id, location) WHERE location IS NOT NULL`. Added as a
  lightweight migration that **fails soft** while duplicates still exist (matching the
  existing `billing_documents` partial-index pattern) and succeeds on a boot after the
  merge has run. This is the recurrence backstop.

## Part 1 — Prevent (receiving upsert)

In `create_receipt` (`pellet.py:1557`), before constructing a `PelletLot`:

1. Resolve the receipt's office (`r.location`).
2. Look up an existing lot for `(qualgen_lot_number.strip(), dose_type_id)` whose office
   matches `r.location` — matching on the `PelletLot.location` column when set, with a
   fallback to a `PelletStock`-location join (so it works during the transition before
   the backfill runs).
3. **If found → reuse it:** add the received doses to that lot's stock at `r.location`
   via `_get_or_create_stock` + `_adjust_stock(+doses)`; if the existing lot's
   expiration is the placeholder (2099 / `UNKNOWN_EXP`) and the receipt carries a real
   one, update it; accumulate `doses_originally_received += doses_received`; fill
   `unit_cost`/`cost_per_dose`/`receipt_id` if the existing lot lacks them; set
   `location` if null. Audit `lot_received` against the existing lot (so the receipt is
   still recorded).
4. **If not found → create as today,** additionally stamping `location = r.location`.

Also re-key the Smartsheet import's `lots_by_number` map to `(number, dose_type,
location)` so a manual re-run reuses rather than re-creates. (Lower priority — the live
recurrence path is `create_receipt`.)

## Part 2 — Clean-up migration (one-time, audited, idempotent)

A script run via a Cloud Run job (the backend image reaches the private-IP DB), with a
`--dry-run` mode that reports the plan without writing.

**Step 0 — backfill `location`.** For every lot with `location IS NULL`, set it to: its
stock row's location (if exactly one), else its receipt's location, else the modal
location of the visits its doses belong to. Flag (do not guess) any lot whose
references span >1 office.

**Step 1 — group** all lots by `(qualgen_lot_number, dose_type_id, location)`.

**Step 2 — per group with >1 record:**
1. **Single-office verification:** confirm every lot in the group has all its stock +
   doses at this one office. If any lot's references span offices, **skip the group and
   report it** for manual handling (never silently mis-merge controlled stock).
2. **Choose canonical:** prefer a receipt-backed lot (`receipt_id` not null) with a real
   expiration (≠ placeholder 2099); tie-break by earliest `received_at`. If none is
   receipt-backed, choose the earliest `received_at`.
3. **Re-point the 6 FKs** from each duplicate → canonical:
   - `PelletVisitDose`, `PelletAuditEvent`, `PelletTransfer`, `PelletDisposal`,
     `PelletCountLine`: `UPDATE ... SET lot_id = canonical WHERE lot_id = dup`.
   - `PelletStock` (can't blind-update — unique `(lot_id, location)`): for each duplicate
     stock row, `_get_or_create_stock(canonical, location)` then add the duplicate's
     `doses_on_hand` into it, then **delete** the duplicate stock row.
4. **Carry forward onto the canonical:** real expiration if it held the placeholder;
   `doses_originally_received += sum(duplicates')`; cost/receipt if missing.
5. **Delete** the now-FK-free duplicate `PelletLot` rows.
6. **Audit** `lot_merged` (actor `system:lot-dedup`) with detail `{canonical_lot_id,
   merged_lot_ids, merged_doses_on_hand, merged_receipt_ids}`.

**Step 3 — invariants (asserted before commit, per run):**
- Total `PelletStock.doses_on_hand` (summed across all rows) is **unchanged**.
- Total `PelletLot.doses_originally_received` is **unchanged**.
- Total `PelletVisitDose` count with a non-null lot is unchanged (re-pointed, not lost).
- After the run, **no `(qualgen_lot_number, dose_type_id, location)` has >1 lot**.

The run is wrapped in one transaction (all-or-nothing); a failed assertion rolls back.
Re-running is a **no-op** (groups already have a single lot → nothing to merge).

## Error handling

| Case | Behavior |
|---|---|
| Lot whose stock/doses span >1 office (backfill ambiguous) | flagged, **not** merged; reported for manual review |
| Group with only placeholder/Smartsheet lots (no receipt) | merge into earliest; expiration stays placeholder until a real receipt updates it |
| Invariant assertion fails | whole run rolls back; nothing written |
| Unique index creation while dups remain | fails soft at boot (logged), succeeds after merge |
| Receiving a lot that exists at a *different* office | creates a new per-office lot (correct under model B) |

## Testing

Prevent (pytest):
- Receiving the same `(number, dose_type)` twice at the **same** office → **one** lot,
  stock summed, `doses_originally_received` accumulated; placeholder expiration replaced
  by the real one.
- Same lot received at **two different** offices → **two** lots (model B).
- The partial unique index rejects a second same-key lot once data is clean.

Merge migration (pytest against synthetic duplicates):
- 3 duplicate lots (one Smartsheet placeholder w/ doses, two receipt-backed w/ stock) →
  1 canonical; all 6 FK types re-pointed; stock summed per location; totals preserved;
  duplicates deleted; `lot_merged` audit written.
- Idempotency: second run makes no changes.
- Single-office guard: a lot with doses at two offices is skipped + reported.
- Invariant guard: a deliberately broken merge rolls back.

## Files

- Modify: `backend/app/models/pellet.py` (`PelletLot.location` column)
- Modify: `backend/app/database.py` (lightweight migration: add column + the fail-soft
  partial unique index)
- Modify: `backend/app/routers/pellet.py` (`create_receipt` upsert + `location` stamping)
- Create: `backend/scripts/pellet_lot_dedup.py` (the merge migration, `--dry-run`)
- Modify: `backend/scripts/pellet_smartsheet_history_import.py` (re-key `lots_by_number`)
- Test: `backend/tests/test_pellet_lot_dedup.py` (prevent + merge)

## Sequence (deploy/run order)

1. Ship the `location` column + `create_receipt` upsert + the fail-soft unique index.
2. Dedup migration **dry-run** (Cloud Run job) → review the per-group plan.
3. Dedup migration **apply** → merges, audited, invariants asserted.
4. Confirm the unique index is now active; then run the **physical count** against the
   clean, single records.
