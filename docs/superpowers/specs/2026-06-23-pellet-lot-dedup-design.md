# Pellet Lot Dedup — Design

**Date:** 2026-06-23
**Module:** Pellet (DEA Schedule III inventory)
**Status:** Approved — **REVISED 2026-06-24 to Model A** (see revision note below)

## ⚠ REVISION 2026-06-24 — switched from Model B (per-office lots) to Model A (one shared lot)

The Model-B dry-run skipped 9 of 10 duplicate groups because their lots' doses
span offices. A read-only investigation showed that's mostly a **data-quality
artifact, not real multi-office dispensing**: `visit.location` is unreliable
(White Plains 5,052 visits vs Brandywine 203 vs Arlington 22 — `white_plains`
is effectively a default), a lot's stock office often ≠ its dominant-dose office,
and only ~19% of doses on multi-office lots (177/945) are at a non-dominant
office, mostly 1–3-dose stragglers. Only 150/1,206 patients are genuinely
multi-office. So **per-office lot identity rests on bad location data.**

**Model A:** one `PelletLot` per `(qualgen_lot_number, dose_type_id)`, with stock
still tracked per office via `PelletStock` (lot × location — unchanged). Cross-office
doses simply hang off the one shared lot. Concrete deltas from the text below:
- **Merge key** = `(qualgen_lot_number, dose_type_id)` — **drop office/location**.
- **`verify_manifest`** merges a freshly-verified lot into the canonical matching
  `(number, dose_type)` regardless of office.
- **Migration**: group by `(number, dose_type)`; **REMOVE the single-office guard**
  and the location-backfill dependency; `merge_lot` is unchanged (it already sums
  stock per office).
- **`PelletLot.location`** becomes informational (the receiving office stamped at
  receipt time), **not** part of lot identity. Keep the column; stop using it for
  grouping.
- **Tests**: same lot received+verified at two offices → **ONE** lot with two stock
  rows (was two lots under B); migration consolidates all duplicate groups.

Everything else below stands; where it says "per office" / "single-office guard" /
"merge key includes location," apply the deltas above.

## Goal

End duplicate pellet lots: **prevent** new ones at receiving, and **merge** the
existing duplicates — keyed by lot number + strength (one shared lot, stock per
office) — preserving every stock total and chain-of-custody link, with a full
audit trail.

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
   (not derived) — used as the merge key and stamped at receipt time.
3. **Delete** duplicate lot rows after re-pointing (not mark-inactive) — all their
   references move to the canonical, so the rows carry no remaining data.

## Architecture

Two independent pieces + an ordering:

- **Prevent** (code, ships first): `verify_manifest` merges a freshly-verified lot into
  the pre-existing canonical for its `(lot #, strength, office)`, reusing the same merge
  helper as the migration — so no duplicate survives verification.
- **Clean up** (one-time migration): merge existing duplicates, audited, idempotent,
  dry-run first, totals-preserved.
- **Sequence:** prevent → dry-run merge → apply merge → physical count.

### New column + shared merge helper

- `PelletLot.location` — `String(40)`, nullable (added via the lightweight-migration
  `needed` list). Backfilled by the merge migration; stamped on every new lot at receipt
  time (`create_receipt` sets `location = r.location`).
- **`merge_lot(db, src, dst, *, actor)`** — a shared service
  (`backend/app/services/pellet/lot_merge.py`) used by BOTH `verify_manifest` (live
  prevention) and the one-time migration (cleanup). It re-points the 6 FK references,
  sums stock per location, carries forward expiration / `doses_originally_received` /
  cost, deletes `src`, and writes a `lot_merged` audit. One implementation, two callers.

**No hard unique index.** The two-step receive→verify flow means a freshly-created
(unverified) lot legitimately duplicates a canonical until it's verified-and-merged, so a
DB-level unique constraint would block normal receiving. The verify-time merge is the
enforcement instead.

## Part 1 — Prevent (verify-time merge)

The receive flow is two-step: `create_receipt` (`pellet.py:1459`) creates a lot record
per receipt line; `verify_manifest` (`pellet.py:1610`) is what credits each lot's
`doses_originally_received` into stock at `r.location` (the loop at `pellet.py:1663`:
`_get_or_create_stock(l.id, r.location)` + `_adjust_stock(+l.doses_originally_received)`).
So dedup at verify, **after** stock is credited.

In `create_receipt`: the only change is to **stamp `location = r.location`** on each new
`PelletLot` (office explicit from creation).

In `verify_manifest`, immediately after the existing per-lot stock-credit loop, for each
just-verified lot `l`:
1. Look for a pre-existing canonical lot with the same `(qualgen_lot_number,
   dose_type_id, location=r.location)`, other than `l`, preferring an earlier
   `received_at`.
2. **If found → `merge_lot(db, src=l, dst=canonical, actor=by)`:** re-points l's stock
   (summed into the canonical's row at this location), doses, audit, transfers,
   disposals, and count-lines onto the canonical; accumulates
   `doses_originally_received`; carries a real expiration onto the canonical if it held
   the placeholder; fills cost/receipt if the canonical lacks them; deletes `l`. Net:
   the canonical's stock rose by the received amount, no duplicate survives.
3. **If not found →** `l` is the canonical for this office; leave it.

An unverified duplicate (received, not yet verified) holds no stock and is harmless until
it's verified-and-merged (or swept by the one-time migration).

Also re-key the Smartsheet import's `lots_by_number` map to `(number, dose_type,
location)` so a manual re-run reuses rather than re-creates. (Lower priority — the live
recurrence path is receiving.)

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
| Receiving a lot that exists at a *different* office | a new per-office lot, not merged (correct under model B) |
| Lot received but never manifest-verified | duplicate persists with no stock until verified-and-merged or swept by the migration (harmless) |

## Testing

Prevent (pytest):
- Receive + verify the same `(number, dose_type)` at the **same** office twice → after
  the second verify there is **one** lot, its stock = sum of both deliveries,
  `doses_originally_received` accumulated, a placeholder expiration replaced by the real
  one.
- Receive + verify the same lot at **two different** offices → **two** lots (model B),
  each with its own stock.
- An unverified duplicate (received, not yet verified) does NOT block receiving and gets
  merged on verification.

Merge migration (pytest against synthetic duplicates):
- 3 duplicate lots (one Smartsheet placeholder w/ doses, two receipt-backed w/ stock) →
  1 canonical; all 6 FK types re-pointed; stock summed per location; totals preserved;
  duplicates deleted; `lot_merged` audit written.
- Idempotency: second run makes no changes.
- Single-office guard: a lot with doses at two offices is skipped + reported.
- Invariant guard: a deliberately broken merge rolls back.

## Files

- Modify: `backend/app/models/pellet.py` (`PelletLot.location` column)
- Modify: `backend/app/database.py` (lightweight migration: add the `location` column)
- Create: `backend/app/services/pellet/lot_merge.py` (shared `merge_lot` helper)
- Modify: `backend/app/routers/pellet.py` (`verify_manifest` calls `merge_lot`;
  `create_receipt` stamps `location`)
- Create: `backend/scripts/pellet_lot_dedup.py` (the merge migration, `--dry-run`, uses
  `merge_lot`)
- Modify: `backend/scripts/pellet_smartsheet_history_import.py` (re-key `lots_by_number`)
- Test: `backend/tests/test_pellet_lot_dedup.py` (prevent + merge)

## Sequence (deploy/run order)

1. Ship the `location` column + the `merge_lot` helper + the `verify_manifest` merge +
   `create_receipt` location stamping.
2. Dedup migration **dry-run** (Cloud Run job) → review the per-group plan.
3. Dedup migration **apply** → merges, audited, invariants asserted.
4. Then run the **physical count** against the clean, single records.
