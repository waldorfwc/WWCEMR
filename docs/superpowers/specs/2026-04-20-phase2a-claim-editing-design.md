# Phase 2a — Claim & Service-Line Editing

**Date:** 2026-04-20
**Project:** wwc-era-project
**Depends on:** Phase 2a0 (`require_group` guard, BILLING list), Phase 2a00 (admin user manager)
**Blocks:** Phase 2b (claim import from uploaded docs — e.g., "Claims Analysis 2025Q2")

## Goal

Let billing + admin users edit claims, service lines, and their adjustments through side-drawer UIs on `ClaimDetail.jsx`, backed by granular REST endpoints. Backend enforces `balance = billed − contractual − other − paid − pt_resp` as a computed field on every mutation; all other money and routing fields are freeform. The existing ERA-parsed data becomes human-editable without erasing provenance fields.

## Workflow

1. Billing/admin user opens a claim at `/claims/:id`.
2. Clicks "Edit claim" in the header → side drawer slides in with all editable claim fields grouped into sections (Identifiers, Routing, Dates, Provider, Patient, Status/Notes, Money, Claim Adjustments).
3. Edits any subset; the Money section shows a live read-only "Balance: $X (computed)" preview. Adjustment rows have inline edit (✎) and delete (✗); "+ Add adjustment" appends a blank row.
4. Clicks Save → drawer fires a sequence of REST calls (claim PATCH → new adjs POST → edited adjs PATCH → deleted adjs DELETE), shows a spinner, then closes with a `✓ Claim saved` toast. On any failure, sequence stops, error banner shows progress (`"2 of 4 applied. Retry?"`), Retry resumes from the failed step.
5. Service lines: "Edit line" on a row opens `EditServiceLineDrawer` with the same pattern (including SL adjustment CRUD). "+ Add line" above the table opens the drawer in add-mode (POST creates the line, then nested POSTs for any adjustments). Edit-mode also has a "Delete" button with a confirm dialog.
6. All mutations write one `AuditLog` row per resource change via existing `log_action`.

## Non-goals

- Claim creation from scratch (deferred to Phase 2b — claim import).
- Claim deletion (use `status = written_off`).
- Patient CRUD from the drawer (picker only selects existing patients).
- Payer autocomplete / master table (freeform string).
- Denials editing (still read-only; tied to appeal workflow, separate feature).
- Editing provenance fields: `era_file_id`, `raw_clp_segment`, `statement_date`, `received_date`, `claim_filing_indicator`, `check_amount`, `billing_provider_npi`, `patient_control_number`.
- Bulk edit across multiple claims.
- Optimistic UI (explicitly pessimistic — spinner + disabled inputs).
- One-click undo (audit log records changes; revert is a future feature).
- Frontend unit/component tests (no React Testing Library harness in repo; manual checklist instead).
- Cross-field soft checks (no "service-line total ≠ claim total" warnings).
- Admin-vs-billing permission split within the editing UI (both groups get full edit).

## Backend

Four routers, all mounted in `main.py` with `dependencies=BILLING` (the existing `[Depends(auth.require_group("admin", "billing"))]` list). Clinical users are already blocked at the include_router level and won't see these endpoints.

### Shared balance utility: `backend/app/services/claim_math.py`

```python
from decimal import Decimal
from app.models.claim import Claim

def recompute_balance(claim: Claim) -> None:
    """Set claim.balance = billed - contractual - other - paid - pt_resp. Mutates in place."""
    claim.balance = (
        (claim.billed_amount or 0)
        - (claim.contractual_adjustment or 0)
        - (claim.other_adjustment or 0)
        - (claim.paid_amount or 0)
        - (claim.patient_responsibility or 0)
    )
```

Called after any write that touches claim money fields. Does not commit — caller commits.

### Expanded router: `backend/app/routers/claims.py`

**`PATCH /api/claims/{claim_id}`** — expand allow-list from 8 fields to all editable:

```
status, notes, patient_id, claim_number, payer_claim_number,
payer_name, payer_id, subscriber_id, group_number, insurance_order,
date_of_service_from, date_of_service_to, check_number, check_date,
rendering_provider_name, rendering_provider_npi,
billed_amount, allowed_amount, paid_amount,
patient_responsibility, contractual_adjustment, other_adjustment
```

- `balance` removed from allow-list; always computed via `recompute_balance(claim)` after apply.
- `patient_id`: if provided, must resolve to an existing patient or 422.
- Enum fields (`status`, `insurance_order`) validated against their SAEnum; 422 on bad value.
- Date fields accept ISO `YYYY-MM-DD` strings or `null`.
- Dollar fields accept number or decimal-parseable string; reject non-numeric. Negative allowed.
- Audit row `UPDATE claim resource_id=<id> old_values=<changed fields before> new_values=<changed fields after>`.

### New router: `backend/app/routers/service_lines.py`

Prefix: `/api` (endpoints use claim-scoped and resource-scoped paths).

**`POST /api/claims/{claim_id}/service-lines`**
- Body: any subset of the 14 editable SL fields (`procedure_code`, `modifier_1..4`, `revenue_code`, `units`, `description`, `date_of_service_from`, `date_of_service_to`, `billed_amount`, `allowed_amount`, `paid_amount`, `patient_responsibility`, `contractual_adjustment`, `other_adjustment`, `diagnosis_codes`).
- Empty body allowed (creates a blank line).
- 404 if claim not found.
- Audit `CREATE service_line resource_id=<new id>`.
- Returns the new SL dict (same shape as `_claim_to_dict` nested item).
- Calls `recompute_balance(claim)` — no-op for a blank line, kept for consistency.

**`PATCH /api/service-lines/{line_id}`**
- Body: any subset of the 14 editable SL fields.
- 404 if line not found.
- After apply, looks up parent claim and calls `recompute_balance(claim)` (claim totals don't depend on SL money, but we stay consistent — future-proofing).
- Audit `UPDATE service_line` with old/new.
- Returns updated SL dict.

**`DELETE /api/service-lines/{line_id}`**
- 404 if line not found.
- ORM cascade removes `service_line_adjustments`.
- `recompute_balance(parent_claim)`.
- Audit `DELETE service_line resource_id=<id>`.
- Returns `{"ok": true}`.

### New router: `backend/app/routers/claim_adjustments.py`

**`POST /api/claims/{claim_id}/adjustments`**
- Body: `group_code`, `reason_code`, `amount` (required); `quantity`, `reason_description` (optional).
- 404 if claim not found.
- Does NOT auto-sum into `claim.contractual_adjustment` or `claim.other_adjustment` (freeform per design Q4).
- Does NOT recompute claim balance (adjustments don't touch claim money fields).
- Audit `CREATE claim_adjustment`.
- Returns the new adjustment dict.

**`PATCH /api/claim-adjustments/{adj_id}`**
- Body: any subset of the 5 editable fields.
- 404 if adjustment not found.
- No balance recompute.
- Audit `UPDATE claim_adjustment` with old/new.
- Returns updated adjustment dict.

**`DELETE /api/claim-adjustments/{adj_id}`**
- 404 if not found.
- No balance recompute.
- Audit `DELETE claim_adjustment resource_id=<id>`.
- Returns `{"ok": true}`.

### New router: `backend/app/routers/service_line_adjustments.py`

Mirrors `claim_adjustments.py`, scoped to `service_line_id`:
- `POST /api/service-lines/{line_id}/adjustments`
- `PATCH /api/service-line-adjustments/{adj_id}`
- `DELETE /api/service-line-adjustments/{adj_id}`

Same fields, same validation, same audit pattern. No claim-balance recompute.

### Model changes

None. All four tables (`claims`, `service_lines`, `claim_adjustments`, `service_line_adjustments`) already exist with every field we need.

### main.py wiring

```python
from app.routers import imports, claims, patients, denials, appeals, eob, audit, \
    service_lines, claim_adjustments, service_line_adjustments
...
app.include_router(claims.router,                    prefix="/api", dependencies=BILLING)
app.include_router(service_lines.router,             prefix="/api", dependencies=BILLING)
app.include_router(claim_adjustments.router,         prefix="/api", dependencies=BILLING)
app.include_router(service_line_adjustments.router,  prefix="/api", dependencies=BILLING)
```

## Frontend

### New: `frontend/src/components/EditClaimDrawer.jsx`

Opens from a new "Edit claim" button in the `ClaimDetail.jsx` header. Slides in from the right, ~520px wide, semi-transparent backdrop; claim detail visible underneath on wider screens.

Sections (in order):
- **Identifiers:** `claim_number`, `payer_claim_number`
- **Routing:** `payer_name`, `payer_id`, `subscriber_id`, `group_number`, `insurance_order` (select: primary/secondary/tertiary/patient)
- **Dates:** `date_of_service_from`, `date_of_service_to`, `check_number`, `check_date`
- **Provider:** `rendering_provider_name`, `rendering_provider_npi`
- **Patient:** `<PatientPicker>` (autocomplete on `/api/patients?search=...`)
- **Status & Notes:** `status` (select from ClaimStatus enum), `notes` (textarea)
- **Money:** `billed_amount`, `allowed_amount`, `paid_amount`, `patient_responsibility`, `contractual_adjustment`, `other_adjustment`, then a read-only row `Balance: $X.XX (computed)` with a small lock icon.
- **Claim Adjustments:** list of rows `[group_code] [reason_code] $[amount] [description] ✎ ✗`. "+ Add adjustment" appends a blank row in inline-edit mode.

Footer: `[Cancel]  [Save]`. Save disabled while saving, spinner on button.

### New: `frontend/src/components/EditServiceLineDrawer.jsx`

Opens from per-row "✎ Edit line" button (edit mode) or table-top "+ Add line" button (add mode).

Sections:
- **Code:** `procedure_code`, `revenue_code`, `description`
- **Modifiers:** `modifier_1..4` (4 small inputs)
- **Dates:** `date_of_service_from`, `date_of_service_to`
- **Quantity:** `units`
- **Diagnosis codes:** chip input — comma-separated text box that splits on save into the JSON array; displays existing codes as removable chips.
- **Money:** `billed_amount`, `allowed_amount`, `paid_amount`, `patient_responsibility`, `contractual_adjustment`, `other_adjustment`
- **SL Adjustments:** same inline pattern as claim adjustments.

Footer (edit mode): `[Delete]  [Cancel]  [Save]`. Delete has a confirm dialog.
Footer (add mode): `[Cancel]  [Save]`.

### Shared components

- **`<PatientPicker>`** (`frontend/src/components/PatientPicker.jsx`) — autocomplete combobox calling `/api/patients?search=...&per_page=10`. If a version already exists in the codebase, reuse; otherwise create.
- **`<MoneyInput>`** (`frontend/src/components/MoneyInput.jsx`) — `<input type="number" step="0.01">` with `$` prefix styling. Used ~30 places.
- **`<AdjustmentList>`** (`frontend/src/components/AdjustmentList.jsx`) — shared list UI for both drawer types. Takes a `kind` prop (`"claim"` | `"service-line"`) and `parentId`; owns the op-tagged adjustment array. Parent drawer passes the array through to save orchestration.

### Entry-point edits on `ClaimDetail.jsx`

1. Header: add `<button className="btn-primary">Edit claim</button>` next to the EOB PDF button.
2. Service-lines table: add a trailing column with a `✎ Edit line` button per row; add `+ Add line` button above the table.
3. Drawer open/close state via `useState` on `ClaimDetail`.

### Local form state shape

**Claim drawer:**
```js
{
  fields: { /* all 22 editable claim fields */ },
  adjustments: [
    { id: "uuid", op: "none",    /* original fields */ },
    { id: "uuid", op: "edited",  /* edited fields */ },
    { id: "uuid", op: "deleted" },
    { tempId: 1, op: "new",      /* new-row fields */ },
  ]
}
```

Service-line drawer has the same shape with SL fields + SL adjustments.

### Save orchestration

**Claim drawer Save (sequential, not parallel):**
1. If any claim field changed → `PATCH /api/claims/{id}` with changed subset.
2. For each `op === "new"` adjustment → `POST /api/claims/{id}/adjustments`.
3. For each `op === "edited"` → `PATCH /api/claim-adjustments/{adjId}`.
4. For each `op === "deleted"` → `DELETE /api/claim-adjustments/{adjId}`.

**Service-line drawer Save (edit mode):**
1. `PATCH /api/service-lines/{id}` if changed.
2. New / edited / deleted SL adjustments in that order.

**Service-line drawer Save (add mode):**
1. `POST /api/claims/{claimId}/service-lines` → returns new line with `id`.
2. For each SL adjustment the user added → `POST /api/service-lines/{newId}/adjustments`.

On full success:
- `queryClient.invalidateQueries({ queryKey: ['claim', claimId] })` → ClaimDetail refetches.
- Drawer closes.
- Toast: `✓ Claim saved` or `✓ Service line saved`.

On partial failure (any step throws):
- Stop sequence.
- Keep drawer open.
- Red error banner: `"Save failed at step N of M: <message>. <N-1> of <M> changes applied. [Retry]"`.
- Successful prior ops stay persisted (not rolled back).
- Local state retains unsaved ops with their `op` markers → Retry resumes.
- Cancel discards local state (prior successful ops remain in server state; user sees them in the read-only view on close).

### Mutation hooks

- `useClaimEdit(claimId)` → `{ save, saving, error, step }` — wraps the claim-drawer save sequence.
- `useServiceLineEdit(lineId | null)` → same shape; `null` means add mode.

Both hooks internally call React Query mutations but expose a single `save({fields, adjustments})` function that orchestrates the sequence.

## Files touched

**Backend — created:**
- `backend/app/services/claim_math.py`
- `backend/app/routers/service_lines.py`
- `backend/app/routers/claim_adjustments.py`
- `backend/app/routers/service_line_adjustments.py`
- `backend/tests/test_claim_math.py`
- `backend/tests/test_claim_edit.py`
- `backend/tests/test_service_lines.py`
- `backend/tests/test_claim_adjustments.py`
- `backend/tests/test_service_line_adjustments.py`

**Backend — modified:**
- `backend/app/routers/claims.py` — expand PATCH allow-list, call `recompute_balance`
- `backend/app/main.py` — include 3 new routers with BILLING guard
- `backend/tests/conftest.py` — add `clinical_client` fixture if not already present

**Frontend — created:**
- `frontend/src/components/EditClaimDrawer.jsx`
- `frontend/src/components/EditServiceLineDrawer.jsx`
- `frontend/src/components/AdjustmentList.jsx`
- `frontend/src/components/MoneyInput.jsx`
- `frontend/src/components/PatientPicker.jsx` (if not already present)
- `frontend/src/hooks/useClaimEdit.js`
- `frontend/src/hooks/useServiceLineEdit.js`

**Frontend — modified:**
- `frontend/src/pages/ClaimDetail.jsx` — Edit claim button, per-row Edit line buttons, Add line button, drawer state

## Verification

### Automated (pytest)

Run `pytest backend/tests/` — all existing tests plus new suites below pass.

### Manual UI checklist

- [ ] Open a claim → click "Edit claim" → drawer opens with current values.
- [ ] Edit each section (one field each), Save → toast shows, drawer closes, detail view reflects changes.
- [ ] Edit `billed_amount` → detail view `Balance` recalculates correctly.
- [ ] Add a claim adjustment, Save → appears in detail and in audit log.
- [ ] Edit an existing claim adjustment, Save → changes reflected.
- [ ] Delete a claim adjustment, Save → removed from list.
- [ ] Click "+ Add line" → drawer opens empty → fill in, Save → new row appears in service-lines table.
- [ ] Click "✎ Edit line" on a row → edit money fields → Save → values update.
- [ ] Click "Delete" in edit-line drawer → confirm → row gone, any SL adjustments also gone.
- [ ] Partial-failure test: in browser devtools block one mid-sequence request → red banner shows `N of M applied` → Retry completes the rest.
- [ ] Cancel button discards unsaved form state.
- [ ] As clinical user (switch group via sqlite), hitting any edit endpoint → 403; UI edit buttons not reachable because `/claims` route itself is BILLING-gated.

## Tests (backend)

### `test_claim_math.py`
1. `test_recompute_balance_basic` — billed 100, contractual 10, paid 80, pt_resp 5 → balance 5.
2. `test_recompute_balance_zeros` — all zeros → balance 0.
3. `test_recompute_balance_negative_adjustments` — negative contractual = reversal → increases balance.
4. `test_recompute_balance_idempotent` — calling twice produces same result, no side effects on other fields.

### `test_claim_edit.py` (expanded PATCH)
5. `test_patch_money_fields_recomputes_balance` — send billed=200 → balance recomputed.
6. `test_patch_balance_in_body_is_ignored` — send balance=999 → stored balance is computed, not 999.
7. `test_patch_each_field_group` — parametrized over (money / dates / routing / identifiers / provider / status_notes); each set accepted.
8. `test_patch_bad_status_enum_422`.
9. `test_patch_bad_insurance_order_enum_422`.
10. `test_patch_nonexistent_patient_id_422`.
11. `test_patch_missing_claim_404`.
12. `test_patch_audit_row_written_with_changed_fields_only`.

### `test_service_lines.py`
13. `test_post_service_line_full_fields_creates_and_returns`.
14. `test_post_service_line_empty_body_creates_blank`.
15. `test_post_service_line_missing_claim_404`.
16. `test_patch_service_line_each_field_group`.
17. `test_patch_service_line_missing_404`.
18. `test_delete_service_line_cascades_adjustments` — seed 2 adj rows, delete line, confirm 0 adj rows remain.
19. `test_delete_service_line_missing_404`.
20. `test_service_line_writes_audit_rows_per_op`.
21. `test_service_line_write_recomputes_parent_balance`.

### `test_claim_adjustments.py`
22. `test_post_claim_adjustment_creates_and_does_not_change_balance` — key freeform-behavior test.
23. `test_post_claim_adjustment_missing_claim_404`.
24. `test_patch_claim_adjustment_updates_fields`.
25. `test_patch_claim_adjustment_missing_404`.
26. `test_delete_claim_adjustment_removes_row`.
27. `test_delete_claim_adjustment_missing_404`.
28. `test_claim_adjustment_audit_rows_written`.

### `test_service_line_adjustments.py`
29. `test_post_sl_adjustment_creates_and_does_not_change_claim_balance`.
30. `test_post_sl_adjustment_missing_line_404`.
31. `test_patch_sl_adjustment_updates_fields`.
32. `test_patch_sl_adjustment_missing_404`.
33. `test_delete_sl_adjustment_removes_row`.
34. `test_delete_sl_adjustment_missing_404`.
35. `test_sl_adjustment_audit_rows_written`.

### Auth (one per router)
36. `test_claims_patch_forbidden_for_clinical` — 403 via `clinical_client`.
37. `test_service_lines_forbidden_for_clinical` — 403 on POST/PATCH/DELETE.
38. `test_claim_adjustments_forbidden_for_clinical` — 403.
39. `test_service_line_adjustments_forbidden_for_clinical` — 403.

## Open questions

None blocking.
