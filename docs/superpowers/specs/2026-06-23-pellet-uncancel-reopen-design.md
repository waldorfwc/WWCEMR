# Cancelled-Visit Reopen ("Un-cancel") — Design

**Date:** 2026-06-23
**Module:** Pellet
**Status:** Approved (brainstorm complete)
**Builds on:** `2026-06-23-pellet-reopen-visit-design.md` (the reopen feature, shipped; `cancelled` was deferred from it).

## Goal

Re-enable reopening a `cancelled` pellet visit by treating it as **undoing the
cancellation**: re-pull from inventory exactly the doses whose stock was credited
back at cancel time, then let the visit ride the existing reopen→correct→close
machinery. After the un-cancel step there are no `returned` doses left, which
structurally eliminates the double-return / under-count bugs that caused `cancelled`
to be deferred.

## Background (verified mechanics)

`POST /pellets/visits/{id}/cancel` (`pellet.py:4659`) and `stale_sweep` credit stock
back **only for doses in `("pulled","added")`**, setting those to **`returned`**
(lot_id + quantity preserved, `resolved_at/by` stamped). Doses in `planned`,
`inserted`, `disposed`, `reduced` are **left untouched**. The appt-import auto-cancel
returns no stock (only flips status). Therefore:

- **`returned` is precisely the set of doses whose stock was credited on cancel.**
  Re-pulling exactly those is the exact inverse, regardless of which cancel path ran.
- A visit cancelled from `inserted` has no `returned` doses (its doses stay
  `inserted`, stock still out) → un-cancel correctly moves no stock for it.
- `_adjust_stock(db, stock, -qty)` (`pellet.py:238`) does a conditional decrement and
  raises **409** if the balance is `< qty` — the building block for "block on shortfall."
- `_REOPENABLE_STATUSES` is currently `{"inserted","billed"}`. `reopen_visit`
  (`pellet.py:5909`) currently does NO dose changes. `close_reopen` (`pellet.py:5938`)
  already finalizes `planned/pulled/added`→`inserted` and maps
  `pre_reopen_status=="cancelled"`→`inserted` — already correct, unreachable today.

## Decisions (from brainstorm)

1. **Model:** *undo the cancel* — reopening a cancelled visit re-pulls the `returned`
   doses' stock and restores them so the visit becomes a normal reopened visit.
2. **Stock shortfall:** if any re-pull can't be satisfied, **block the reopen with 409**,
   atomically — nothing changes (visit stays cancelled, stock untouched).

## Scope

### In scope
- A `cancelled`-specific branch in `reopen_visit` that re-pulls `returned` doses
  (restoring them to `pulled`), with per-lot delta audit and atomic 409-on-shortfall.
- Re-add `"cancelled"` to `_REOPENABLE_STATUSES` and the frontend reopen-button gate.
- Replace the obsolete "cancelled reopen is rejected" test; add un-cancel tests.
- Restore the manual's cancelled wording with an un-cancel note.

### Out of scope (YAGNI)
- Any change to `close_reopen` (already handles the cancelled→inserted finalize).
- Any change to `cancel_visit` itself.
- Re-pulling `planned`/`inserted`/`disposed`/`reduced` doses (cancel never credited
  their stock, so there is nothing to reverse).
- Re-deriving the *exact* pre-cancel dose status (`pulled` vs `added`); restoring
  `returned`→`pulled` is sufficient because `close_reopen` finalizes `pulled`→`inserted`.

## Architecture

All new logic lives in **one branch of `reopen_visit`**; everything downstream is reused.

### `reopen_visit` — cancelled branch

When `v.status == "cancelled"` (now allowed), BEFORE flipping to `in_progress`:

```
returned_doses = [d for d in v.doses if d.status == "returned"]
if returned_doses and NOT v.is_historical:
    location = _require_visit_location(v)        # same helper cancel uses
    for d in returned_doses:
        if d.lot_id:
            stock = _get_or_create_stock(db, d.lot_id, location)
            _adjust_stock(db, stock, -(d.quantity))   # 409 if short → whole reopen aborts
            _audit(action="visit_uncancel_repull", lot_id=d.lot_id, location=location,
                   delta_doses=-(d.quantity),
                   summary="Re-pulled <q> <label> from stock (visit un-cancelled)",
                   detail={"visit_id":..., "visit_dose_id":...})
        d.status = "pulled"
        d.pulled_at = now_utc_naive(); d.pulled_by = by
        d.resolved_at = None; d.resolved_by = None
elif returned_doses and v.is_historical:
    for d in returned_doses:                      # historical: no stock movement
        d.status = "pulled"
        d.pulled_at = now_utc_naive(); d.pulled_by = by
        d.resolved_at = None; d.resolved_by = None
```

Then the existing reopen tail runs unchanged: stamp `pre_reopen_status="cancelled"`,
`reopened_at/by/reason`, `status="in_progress"`, audit `visit_reopened`, commit.

**Atomicity:** `reopen_visit` has a single `db.commit()` at the end. Each
`_adjust_stock` decrement either succeeds in-transaction or raises 409; on 409 the
request aborts and the uncommitted re-pulls roll back. No partial un-cancel, no
negative stock.

**Why restore to `pulled` (not `inserted`):** `pulled` means "stock held, not yet
finalized," which is accurate mid-reopen. `close_reopen` already turns
`pulled`→`inserted`. A dose correction on a `pulled` dose works correctly (it holds
stock, so return-old + pull-new reconciles). There are no `returned` doses left after
this branch, so the deferred double-return bug cannot occur.

### Everything downstream (unchanged)

- The reopened visit now matches a reopened inserted visit: doses hold stock.
- **Dose corrections** work as-is.
- **Append** is already MANAGE-gated for reopened visits.
- **`close_reopen`** finalizes `pulled`→`inserted`, `pre_reopen_status=="cancelled"`
  →`inserted`, sets `inserted_at` if null. No change.

### Re-enable + UI + docs

- `_REOPENABLE_STATUSES = {"inserted","billed","cancelled"}`.
- Frontend `PelletPatientDetail.jsx:1041`: `canReopen` array → `['inserted','billed','cancelled']`.
- Manual `reopen-correct-visit` section: restore "or a cancelled one" and add: "Reopening
  a cancelled visit un-cancels it — the doses it returned to stock are pulled back out;
  if there isn't enough on hand, the reopen is blocked until you receive stock."

## Data flow

```
Reopen cancelled:
  POST /pellets/visits/{id}/reopen {reason}
   → reopen_visit: for each `returned` dose → re-pull stock (-qty, 409 if short) → status `pulled`
   → stamp reopen, pre_reopen_status="cancelled", status→in_progress
Correct (optional): existing PATCH dose endpoint (return old + pull new)
Close:
  POST /pellets/visits/{id}/close-reopen
   → finalize `pulled`→`inserted`; pre=="cancelled" → status `inserted`; set inserted_at
```

## Error handling

| Case | Behavior |
|---|---|
| Reopen cancelled, insufficient stock to re-pull a returned dose | 409, atomic — visit stays cancelled, stock unchanged |
| Reopen cancelled with no `returned` doses (e.g. cancelled-from-inserted, or appt-import cancel) | no stock movement; flips to in_progress normally |
| Reopen cancelled, visit has no location but has returned doses w/ lots | `_require_visit_location` raises (same as cancel) |
| Non-MANAGE caller | 403 (existing tier dependency) |
| Historical cancelled visit | returned→pulled, stock-neutral |

## Testing

Backend (pytest, `test_pellet_reopen_visit.py`):
- **Replace** `test_reopen_cancelled_now_rejected` with the happy path: reopen a cancelled
  visit → 200, status `in_progress`, `pre_reopen_status=="cancelled"`.
- Reopen cancelled with a `returned` dose (lot L qty N, stock S) → stock drops to `S-N`,
  dose status `pulled`, a `visit_uncancel_repull` audit row with `delta_doses=-N`.
- **Shortfall is atomic:** returned dose qty N, lot stock `< N` → reopen 409, **stock
  unchanged AND visit still `cancelled`, `reopened_at` still null** (the critical test).
- Reopen cancelled then `close-reopen` → status `inserted`, dose `inserted`, **no extra
  stock movement** beyond the re-pull.
- Cancelled-from-inserted (doses already `inserted`, none `returned`) → reopen moves no
  stock, succeeds.
- Historical cancelled visit reopen → returned→pulled, stock unchanged.
- End-to-end conservation: start with a visit whose dose is `pulled` (stock = `S-N`)
  → `cancel` (stock returns to `S`, dose `returned`) → `reopen` (stock re-pulled to
  `S-N`, dose `pulled`) → `close-reopen` (dose `inserted`, no further stock move). Final
  stock `S-N` — identical to the pulled state before cancel; the cancel↔un-cancel round
  trip is net-zero.

Frontend: build green; reopen button appears on cancelled visits for MANAGE users.

## Files

- Modify: `backend/app/routers/pellet.py` (`reopen_visit` cancelled branch, `_REOPENABLE_STATUSES`)
- Modify: `backend/tests/test_pellet_reopen_visit.py` (replace + add tests)
- Modify: `frontend/src/pages/PelletPatientDetail.jsx` (`canReopen` gate)
- Modify: `backend/app/services/manual_seed.py` (manual wording)
