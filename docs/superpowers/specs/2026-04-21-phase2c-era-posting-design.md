# Phase 2c — ERA 835 Payment Posting (with Claim ID Bootstrap)

**Date:** 2026-04-21
**Project:** wwc-era-project
**Depends on:** Phase 2a (claim/service-line edit drawers, `claim_math.recompute_balance`), Phase 2b (Charge-Analysis-imported Claim records, `import_sessions`, Phase 2b UI state-machine pattern)
**Blocks:** Phase 2d (further Claims Analysis enrichment — status, follow-up, filing method)

## Goal

Ship two independent two-step upload flows on `/imports`: (1) upload a Claims Analysis `.xls` to link each existing Claim to its PrimeSuite Claim ID via `patient_control_number`, creating secondary/tertiary Claim records where Claims Analysis shows them; (2) upload one or more ERA 835 files to post payments, adjustments, and denials strictly matched on `patient_control_number`. No ERA ever creates a new Claim record. The legacy auto-posting path in `era_import_service.py` is retired.

## Workflow

1. User opens `/imports`. Sees amber banner: *"Legacy ERA auto-posting disabled — use the cards below."*
2. **Card 1 — "Link Claim IDs (PrimeSuite Claims Analysis)"**: uploads `Claim Analysis 2026.01.xls`. Preview shows *"937 unique claims · 720 will be linked · 80 already linked · 127 no-match · 8 ambiguous · 2 conflicts · (of which 140 are secondary — secondary Claim records will be created)"*. Commit patches `patient_control_number` on primary matches and creates new Claim rows for secondaries/tertiaries.
3. **Card 2 — "ERA 835 Payment Posting"**: drops one or more `.835` files. Preview card shows per-file breakdown + combined totals. Commit posts payments + adjustments + denials to matched claims only.
4. Opening a claim in Phase 2a's edit drawer after posting shows the updated `status`, new Payment rows (list in a "Payment History" section), Denials, and recomputed balance.

## Non-goals

- Claims Analysis status / follow-up / filing method enrichment — Phase 2d.
- Auto-resolve ambiguous matches or conflicts — user reconciles manually via edit drawer.
- Create patients from ERA data — still Charge Analysis's job.
- Create primary claims from ERA data — legacy path retired.
- Fuzzy ERA matching — strict `patient_control_number` only.
- Auto-process reversals (CLP02=22 or negative CAS) — flagged, not posted.
- ModMed EMA `CB`-prefix claims — detected in CLP07 and skipped.
- Check-level balancing (sum of posts == check amount) — future.
- Backfill of ~400 historical `.835` files on disk — user uploads going forward.
- ZIP archive upload — users drop multiple individual files.
- Optimistic concurrency — single-user assumption.
- Removing the legacy ERA drop zone — keep UI but short-circuit behavior.
- Deleting `era_import_service.py` — neutralize in place; delete in Phase 2e.

## Backend

Two new routers, two new services, one parser reused, one legacy neutralized. All routers behind `BILLING` guard.

### Part 1 — Claim ID Bootstrap

#### `backend/app/services/claims_analysis_matcher.py` (pure; no DB writes)

**Dataclasses:**
```python
@dataclass
class ClaimsAnalysisRow:
    patient_external_id: str           # "Patient ID"
    claim_id: str                       # "Claim ID"
    dos: Optional[date]                 # "Date of Service"
    claim_amount: Decimal               # "Claim Amount"
    insurance_priority: str             # "Primary" | "Secondary" | "Tertiary" | "Patient"

@dataclass
class ClaimsAnalysisGroup:
    patient_external_id: str
    claim_id: str
    dos: date
    total_amount: Decimal               # sum of line-level amounts in the group
    row_count: int
    insurance_priority: Literal["primary", "secondary", "tertiary", "patient"]
    internal_claim_id: str              # f"{claim_id}P{patient_external_id}"

@dataclass
class ClaimsAnalysisImport:
    groups: List[ClaimsAnalysisGroup]
    source_filename: str
    total_rows: int
    skipped_rows: int
    issues: List[ParseIssue]            # reuse Phase 2b ParseIssue
```

**Parse (`parse(path)`):**
1. `pd.read_excel(path, sheet_name=0)`.
2. Required columns: `Patient ID`, `Claim ID`, `Date of Service`, `Claim Amount`, `Insurance Priority`. Missing → `ValueError` → 422.
3. Drop rows with null Patient ID or Claim ID (counted into `skipped_rows`, one issue each).
4. Group by `(Patient ID, Claim ID)`. Sum `Claim Amount` into `total_amount`. First-row wins for DOS and `Insurance Priority` (warn if group has mixed priorities).
5. Normalize `insurance_priority` to lowercase. Values outside the expected set → warning, default to `"primary"`.
6. Return `ClaimsAnalysisImport`.

**Matcher (`match_groups_to_claims(db, groups) -> List[MatchResult]`):**
```python
@dataclass
class MatchResult:
    group: ClaimsAnalysisGroup
    status: Literal[
        "will_patch",              # primary match; patient_control_number is null → patch
        "will_create_secondary",   # secondary/tertiary group; create new Claim row
        "already_set",             # primary match; patient_control_number already correct
        "no_patient",              # group's Patient ID not in DB
        "no_claim",                # patient exists but no matching DOS/amount
        "ambiguous",               # multiple candidate claims
        "conflict",                # primary match but existing patient_control_number differs
    ]
    matched_claim_id: Optional[str]             # our internal UUID string
    conflict_existing_value: Optional[str]
```

For each group:
1. Lookup Patient via `Patient.patient_id == group.patient_external_id`. Not found → `no_patient`.
2. Candidates: `Claim.patient_id == patient.id AND date_of_service_from == group.dos AND billed_amount == group.total_amount`.
3. Filter candidates to `insurance_order == group.insurance_priority` when possible — e.g., for a Secondary group, look for an existing `insurance_order=SECONDARY` claim first.
4. If Primary group:
   - Zero primary candidates → `no_claim`.
   - Multiple primary candidates → `ambiguous`.
   - One candidate, `patient_control_number is None` → `will_patch`.
   - One candidate, existing value equals computed `internal_claim_id` → `already_set`.
   - One candidate, existing value differs → `conflict`.
5. If Secondary/Tertiary group:
   - Look for existing Secondary/Tertiary claim with same (patient, DOS, billed).
   - If found with the computed `internal_claim_id` already set → `already_set`.
   - If found with a different value → `conflict`.
   - If not found → `will_create_secondary` (we'll create on commit, inheriting from the primary).
   - If the primary claim doesn't exist yet → `no_claim` (bootstrap primary first).

#### Router: `backend/app/routers/claim_id_bootstrap.py`

**`POST /api/imports/claim-id-bootstrap`** — `multipart/form-data`, `file` field.

Steps: save file under `{upload_dir}/claim_id_bootstrap/{session_id}.xls`; parse; match; stash in `import_sessions`; return preview JSON:
```json
{
  "session_id": "...",
  "source_filename": "Claim Analysis 2026.01.xls",
  "total_rows": 1262,
  "skipped_rows": 3,
  "unique_claims": 937,
  "will_patch": 720,
  "will_create_secondary": 140,
  "already_set": 80,
  "no_patient": 5,
  "no_claim": 127,
  "ambiguous": 8,
  "conflicts": 2,
  "sample_matches": [ /* first 20 MatchResult */ ],
  "issues": [...],
  "expires_at": "..."
}
```

**`POST /api/imports/claim-id-bootstrap/{session_id}/commit`** — empty body.

Steps:
1. `import_sessions.get(session_id)` — 404 if missing/expired.
2. For each `MatchResult`:
   - `will_patch` → set `claim.patient_control_number = group.internal_claim_id`, commit, audit `UPDATE claim` with HIPAA fields.
   - `will_create_secondary` → find the primary Claim (same patient+DOS+billed, `insurance_order=PRIMARY`); if primary missing, mark error and skip. Otherwise create a new Claim row copying `patient_id`, `date_of_service_from/to`, `billed_amount`, `payer_name`, `subscriber_id`, `rendering_provider_name/npi`, `billing_provider_npi` from the primary. Set `insurance_order = SECONDARY` or `TERTIARY` based on group. Set `patient_control_number = group.internal_claim_id`. Set `status = PENDING`. `recompute_balance()`. Copy over service lines with new ServiceLine rows (same procedure_code/modifiers/billed/diagnosis_codes; paid/adjustments/patient_resp start at 0). Commit. Audit `CREATE claim`.
   - All other statuses → no action, counted for response.
3. Purge session. Write one top-level `IMPORT claim_id_bootstrap` audit row.
4. Return:
```json
{
  "source_filename": "...",
  "claims_patched": 720,
  "secondary_claims_created": 140,
  "already_set": 80,
  "unmatched": 132,
  "ambiguous": 8,
  "conflicts": 2,
  "errors": []
}
```

### Part 2 — ERA Payment Posting

#### `backend/app/services/era_poster.py` (pure; no DB writes)

Consumes one or more parsed `EraFile` objects (from existing `era_835.py`). Returns match + post plans.

**Dataclasses:**
```python
@dataclass
class EraClaimMatch:
    era_claim: EraClaim
    status: Literal[
        "matched",
        "unmatched",
        "cb_prefix_skipped",
        "reversal_flagged",
        "malformed_clp01",
        "already_posted",
    ]
    internal_claim_id: Optional[str]
    matched_claim_id: Optional[str]
    reversal_reason: Optional[str]

@dataclass
class EraFilePreview:
    era: EraFile                        # from era_835 parser (header info)
    source_filename: str                # preserved original upload filename
    matches: List[EraClaimMatch]
    parse_errors: List[str]             # from era.parse_errors
    # rollups
    n_matched: int
    n_unmatched: int
    n_already_posted: int
    n_cb_skipped: int
    n_reversals: int
    n_malformed: int
```

**Matching logic** (per EraClaim):
1. CLP01 regex match `^\d+P\d+$`. No match → `malformed_clp01`.
2. CLP07 starts with `"CB"` → `cb_prefix_skipped`.
3. Reversal: `claim_status_code == "22"` OR any CAS amount < 0 → `reversal_flagged` with reason.
4. Strict lookup: `Claim.patient_control_number == CLP01`. Not found → `unmatched`.
5. Check Payment dedup: exists `Payment` with `(claim_id, era_file_id)`? Since we haven't created the EraFile yet at preview time, we check by `(claim_id, check_number, check_date, amount)` — if tuple matches an existing Payment → `already_posted`.
6. Otherwise → `matched`.

**Posting logic** (for each `matched` claim, inside per-claim transaction on commit):
- Create `Payment` row: `claim_id`, `amount=era_claim.paid_amount`, `payment_date=era.check_date or date.today()`, `payer_name=era.payer_name`, `check_number=era.check_number`, `era_file_id`, `posted_by=current_user.email`, `payment_type=INSURANCE_PAYMENT`.
- Update Claim fields:
  - `paid_amount` = `sum(Payment.amount for Payment.claim_id == claim.id)` after insert.
  - `payer_claim_number` = `era_claim.payer_claim_number` **if currently null** (don't overwrite).
  - `check_number` / `check_date` / `era_file_id` ← ERA values (overwrite; latest wins).
  - `contractual_adjustment` += sum of CO-45 from this ERA.
  - `other_adjustment` += sum of non-CO-non-PR adjustments from this ERA.
  - `patient_responsibility` = `era_claim.patient_responsibility` (latest wins).
  - `allowed_amount` = `era_claim.billed_amount - total_CO_45`.
  - `status` = `_determine_claim_status(era_claim)` — copied from legacy.
  - `recompute_balance(claim)`.
- Create `ClaimAdjustment` rows for each claim-level CAS — dedup on `(claim_id, era_file_id, group_code, reason_code)`.
- Service-line linking:
  - For each `EraServiceLine`: find matching ServiceLine where `claim_id == matched_claim_id AND procedure_code == svc.procedure_code`. If multiple matches, prefer one with matching `modifier_1`. If still ambiguous or zero → warning issue; claim-level post still succeeds.
  - On match: update `sl.paid_amount += svc.paid_amount`, `sl.patient_responsibility = svc_pr`, `sl.contractual_adjustment += svc_co_total`. Create `ServiceLineAdjustment` rows for each SVC-level CAS.
- Create `Denial` rows for non-CO-45 CAS — **logic imported from** `era_import_service._create_denials` (reuse `analyze_denial`, `get_carc_info`, `maryland_rules`).
- Audit: `log_action("POST_PAYMENT", "claim", resource_id=str(claim.id), user_name=..., patient_id=str(claim.patient_id), new_values={paid_amount, status, check_number}, description=f"ERA {source_filename} check {era.check_number}")`.

#### Router: `backend/app/routers/era_posting.py`

**`POST /api/imports/era-posting`** — `multipart/form-data`, accepts **multiple** `file` fields (`file: List[UploadFile] = File(...)`).

Steps:
1. Generate `session_id`.
2. For each uploaded file: save to `{upload_dir}/era_posting/{session_id}/{idx}-{original_name}`.
3. Parse each via `era_835.py`. Per-file parse errors collected, don't abort batch.
4. Build `EraFilePreview` per file via matcher.
5. Stash in `import_sessions` with a new multi-file payload shape:
   ```python
   payload = {"previews": List[EraFilePreview]}
   ```
6. Return combined preview JSON:
```json
{
  "session_id": "...",
  "files": [
    {
      "source_filename": "JOHNSHOPKINSHEALTHPLANS_...835",
      "check_number": "17020835",
      "check_amount": 12345.67,
      "check_date": "2025-01-02",
      "payer_name": "Johns Hopkins Health Plans",
      "n_claims": 18,
      "n_matched": 16,
      "n_unmatched": 2,
      "n_already_posted": 0,
      "n_cb_skipped": 0,
      "n_reversals": 0,
      "n_malformed": 0,
      "parse_errors": []
    },
    { ...another file... }
  ],
  "totals": {
    "n_files": 3,
    "combined_check_amount": 45000.00,
    "n_matched": 42,
    "n_unmatched": 4,
    "n_already_posted": 0,
    "n_cb_skipped": 0,
    "n_reversals": 2,
    "n_malformed": 0
  },
  "sample_matches": [ /* first 20 from across all files */ ],
  "issues": [ /* per-file unmatched/reversal/malformed detail */ ],
  "expires_at": "..."
}
```

**`POST /api/imports/era-posting/{session_id}/commit`** — empty body.

Steps:
1. Session lookup. 404 if missing/expired.
2. For each `EraFilePreview`:
   - Create `EraFile` row with `filename`, `file_path`, `payer_name`, `payer_id`, `check_number`, `check_date`, `check_amount`, `transaction_count`, `status = "processed"` or `"partial"` (if parse_errors), `imported_by=current_user.email`.
   - For each `matched` `EraClaimMatch`: run posting logic in a per-claim transaction. Skip non-matched statuses.
3. Write ONE top-level audit `IMPORT era_file resource_id=str(era_file.id)` per source file.
4. Purge session.
5. Return:
```json
{
  "files_processed": 3,
  "claims_posted": 42,
  "claims_already_posted": 0,
  "claims_unmatched": 4,
  "claims_reversal_flagged": 2,
  "claims_cb_skipped": 0,
  "claims_malformed": 0,
  "payments_created": 42,
  "denials_created": 8,
  "errors": []
}
```

### Legacy cleanup

**`backend/app/services/era_import_service.py`** — replace `import_era_file()` body:
```python
def import_era_file(db, era, file_path, imported_by="system"):
    raise NotImplementedError(
        "Legacy ERA auto-import was retired in Phase 2c. "
        "Use POST /api/imports/era-posting for payment posting."
    )
```
All other functions in the module (`_determine_claim_status`, `_create_denials`, `_has_real_denials`, `_import_claim`, helpers) remain — `era_poster.py` imports `_determine_claim_status` and the denial-creation logic directly.

**`backend/app/routers/imports.py`** — in the branch that currently calls `import_era_file(...)`:
```python
try:
    era_file = import_era_file(db, result.era_data, save_path)
    ...
except NotImplementedError as e:
    raise HTTPException(status_code=410, detail={
        "message": str(e),
        "migration_endpoint": "/api/imports/era-posting",
    })
```

### Model changes

**None.** The `Claim`, `Payment`, `Denial`, `ClaimAdjustment`, `ServiceLineAdjustment`, `EraFile`, `Patient` models all have the fields we need. `Payment` already accepts negatives (for reversals, if the user ever unblocks them in the UI). `Claim.patient_control_number` is an existing `String(100)` nullable field — we just start populating it.

## Frontend

### Banner + two new cards on `ImportFiles.jsx`

Top of page (above existing Charge Analysis card):
```
⚠ Legacy ERA auto-posting is disabled. Use the ERA 835 Payment Posting card below.
   The old drop zone still parses ERA files for inspection but will not create claims.
```

**Card 1 (above Card 2): "Link Claim IDs (PrimeSuite Claims Analysis)"**
- Dropzone accepts `.xls`/`.xlsx`.
- Preview card shows counts per match status. Expandable issues list.
- Success card: *"✓ Linked 720 claims · 140 secondary claims created · 132 unmatched · 8 ambiguous · 2 conflicts"*. `[View claims →]`.

**Card 2: "ERA 835 Payment Posting"**
- Dropzone accepts **multiple** `.835` files (`<input type="file" multiple accept=".835,.x12,.edi">`). Drag-drop multiple files too.
- Preview shows combined totals + expandable per-file breakdown (one sub-row per source file with its own counts).
- Expandable issues list per-file: `UNMATCHED · CLP01 ... · billed $X · Claim ID not linked`.
- Success card: *"✓ Posted 42 claims across 3 ERAs · 42 payments · 8 denials · 4 unmatched · 2 reversals flagged"*. `[View claims →]` / `[Upload more ERAs]`.

Both cards use the same state-machine pattern as `ChargeAnalysisPreview` (null → uploading → preview → committing → success/error, with 30-minute session countdown).

Duplicate-and-customize rather than extract a shared component — 3 cards are the right threshold for duplication tolerance.

### Access control

Already enforced at router level (`BILLING`). Clinical users don't see `/imports`.

## Files touched

**Backend — created:**
- `backend/app/services/claims_analysis_matcher.py`
- `backend/app/services/era_poster.py`
- `backend/app/routers/claim_id_bootstrap.py`
- `backend/app/routers/era_posting.py`
- `backend/tests/fixtures/claim_analysis_2026_01.xls` (copy of user's file)
- `backend/tests/fixtures/johns_hopkins_era.835` (copy of user's file)
- `backend/tests/test_claims_analysis_matcher.py`
- `backend/tests/test_claim_id_bootstrap_router.py`
- `backend/tests/test_era_poster.py`
- `backend/tests/test_era_posting_router.py`
- `backend/tests/test_legacy_era_import_disabled.py`

**Backend — modified:**
- `backend/app/main.py` — include `claim_id_bootstrap.router` and `era_posting.router` with BILLING guard.
- `backend/app/services/era_import_service.py` — short-circuit `import_era_file()` to `NotImplementedError`. Keep helpers.
- `backend/app/routers/imports.py` — catch `NotImplementedError` on ERA branch, return 410.

**Frontend — modified:**
- `frontend/src/pages/ImportFiles.jsx` — banner + two new cards.

## Verification

**Automated (pytest):** 167 prior tests + ~43 new = ~210 passing.

**Manual UI checklist:**
- [ ] Banner visible at top of `/imports`.
- [ ] Card 1 upload `Claim Analysis 2026.01.xls` → preview shows match counts. Commit → claims patched, secondary claims created, visible on `/claims`.
- [ ] Card 2 upload one ERA → preview shows per-file breakdown. Commit → payments/denials posted.
- [ ] Card 2 upload multiple ERAs at once (drag-drop 3 files) → preview shows all 3 with combined totals. Commit → all 3 `EraFile` rows created.
- [ ] Re-upload same ERA → preview shows `already_posted` count.
- [ ] Legacy drop zone upload `.835` → 410 Gone error with migration message.
- [ ] Open a posted claim in `/claims/:id` → Phase 2a drawer shows updated status, payment count, denials, balance.
- [ ] As clinical user → `/imports` redirects; endpoints 403.

## Tests (backend)

### `test_claims_analysis_matcher.py` (9 tests)
1. `test_parse_real_fixture` — 937 groups, correct skipped count.
2. `test_parse_missing_required_column_raises`.
3. `test_parse_drops_rows_with_null_patient_or_claim_id`.
4. `test_parse_mixed_priority_in_group_warns`.
5. `test_match_primary_success_will_patch`.
6. `test_match_primary_no_patient`.
7. `test_match_primary_ambiguous`.
8. `test_match_primary_conflict_when_existing_value_differs`.
9. `test_match_secondary_will_create_when_no_existing_secondary`.

### `test_claim_id_bootstrap_router.py` (7 tests)
10. `test_upload_returns_preview_counts`.
11. `test_commit_patches_primary_matches`.
12. `test_commit_creates_secondary_claim_row` — new Claim with `insurance_order=SECONDARY`, same patient+DOS+billed, own `patient_control_number`, service lines copied.
13. `test_commit_audit_rows` — HIPAA fields on each patch/create.
14. `test_commit_404_on_unknown_session`.
15. `test_upload_forbidden_for_clinical`.
16. `test_commit_forbidden_for_clinical`.

### `test_era_poster.py` (12 tests)
17. `test_match_strict_by_patient_control_number`.
18. `test_match_unmatched_when_no_link`.
19. `test_match_malformed_clp01_skipped`.
20. `test_match_cb_prefix_in_clp07_skipped`.
21. `test_reversal_flagged_on_clp02_22`.
22. `test_reversal_flagged_on_negative_cas`.
23. `test_post_creates_payment_row`.
24. `test_post_accumulates_paid_amount_from_payment_sum`.
25. `test_post_recomputes_balance`.
26. `test_post_creates_denial_for_real_denial` — CO-16 → Denial row; uses legacy `analyze_denial`.
27. `test_post_skips_denial_for_co_45`.
28. `test_post_dedup_same_era_skipped` — re-parse of same ERA → `already_posted`.

### `test_era_posting_router.py` (8 tests)
29. `test_upload_single_era_returns_preview`.
30. `test_upload_multiple_eras_combined_preview`.
31. `test_commit_posts_matched_claims`.
32. `test_commit_writes_era_file_record_per_source_file`.
33. `test_commit_per_claim_audit_rows`.
34. `test_commit_top_level_import_audit_row_per_file`.
35. `test_commit_one_file_parse_error_does_not_abort_batch`.
36. `test_upload_and_commit_forbidden_for_clinical`.

### `test_legacy_era_import_disabled.py` (3 tests)
37. `test_import_era_file_raises_not_implemented`.
38. `test_imports_upload_era_returns_410_gone`.
39. `test_legacy_helpers_still_importable` — `_determine_claim_status`, `_create_denials` still reachable from `era_poster`.

**Total: 39 new backend tests.** Full suite becomes `167 + 39 = 206`.

## Open questions

None blocking.
