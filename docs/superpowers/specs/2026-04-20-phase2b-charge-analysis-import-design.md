# Phase 2b — Charge Analysis Import

**Date:** 2026-04-20
**Project:** wwc-era-project
**Depends on:** Phase 2a (Claim + ServiceLine editing; `claim_math.recompute_balance`; existing `/imports/upload` infra + `ImportFiles.jsx`)
**Blocks:** Phase 2c (ERA payment posting to imported claims), Phase 2d (Claims Analysis enrichment: status / follow-up / claim_id)

## Goal

Ship a two-step upload → preview → commit flow on `/imports` that ingests PrimeSuite Charge Analysis `.xls` exports and creates full `Claim` + `ServiceLine` records (with adjustments, patient auto-create, payer/subscriber detail, provider NPIs). Voided rows skipped; duplicates (by `Visit: VisitID`) skipped. A one-time script wipes the unreliable legacy ERA-imported claim data before the first Charge Analysis import. ERA and Claims Analysis imports are deferred to later phases.

## Workflow

1. Admin/billing user runs `python -m app.scripts.reset_claims_data --yes-i-am-sure` once to wipe claim-side tables.
2. Signs in, navigates to `/imports`. Sees a new "Charge Analysis Import (PrimeSuite)" card below the existing ERA upload zone.
3. Drops `Charge Analysis Test4.xls` on the drop zone.
4. Frontend POSTs the file to `/api/imports/charge-analysis`. Backend parses with pandas, returns a preview payload (counts of new / skipped-existing / voided / patients-to-create, plus first 20 sample claims and the full issues list).
5. UI shows a preview card: "512 new · 247 skipped · 12 voided · 23 new patients · 2 errors · 8 warnings" with **Cancel** and **Commit import** buttons plus a 30-minute session expiry countdown.
6. User clicks Commit. Frontend POSTs `/api/imports/charge-analysis/{session_id}/commit`.
7. Backend iterates the parsed claims (one DB transaction per claim), resolves or creates the patient, inserts the claim + service lines, runs `recompute_balance`, writes one audit row per claim plus one `IMPORT` audit row for the whole commit.
8. UI replaces preview with a success card (or partial-failure card if any claims errored).

## Non-goals

- ERA import (unchanged at `/imports/upload`; produces no useful state until Phase 2c).
- Claims Analysis enrichment — Phase 2d.
- In-UI editing of parsed rows before commit (fix the file or edit post-commit via Phase 2a drawer).
- Per-row user selection in preview — commit is all-or-nothing modulo per-claim failure isolation.
- Cross-file dedup reconciliation (first-committed-wins).
- CSV / PDF fallback for Charge Analysis (only `.xls` / `.xlsx`).
- Multi-worker-safe session store (in-memory dict; TODO for Redis).
- Session persistence across restarts.
- Scheduled / automated imports.
- Patient demographic update when a match exists (first-write-wins).
- Payer master table / normalization.
- Multi-tier insurance (secondary claims not created as separate Claim records).
- Automatic claim status derivation (everything starts `pending` until ERA/Claims Analysis updates it).

## File format expected

PrimeSuite Charge Analysis custom export, 45 columns, Sheet1, headers on row 0. Grain: one row = one service line. Claims are identified by `Visit: VisitID`. A reference fixture (`Charge Analysis Test4.xls`) is committed to `backend/tests/fixtures/`.

### Column → model field mapping

| Model field | Excel column | Notes |
|---|---|---|
| `claim.claim_number` | `Visit: VisitID` | Dedup anchor |
| `claim.patient_id` | lookup/create by `Patient: Patient ID` | chart # |
| `claim.date_of_service_from` | `Date: Service date of the Charge` | MM/DD/YYYY → date |
| `claim.payer_name` | `Insurance: Charge Primary Ins. Company` | freeform |
| `claim.payer_id` | NULL | not in file |
| `claim.subscriber_id` | `Insurance: Charge Primary Policy Number` | |
| `claim.group_number` | NULL | user chose to exclude |
| `claim.rendering_provider_name` | `Provider: Rendering` | |
| `claim.rendering_provider_npi` | `Provider: Rendering NPI` | |
| `claim.billing_provider_npi` | `Provider: Billable NPI` | |
| `claim.insurance_order` | `"primary"` (default) | secondary ins not split |
| `claim.status` | `"pending"` (default) | updated by Phase 2c/2d |
| `claim.billed_amount` | sum of `Charge: Gross Charges` across service lines | |
| `claim.paid_amount` | sum of \|`Payment: Net Primary Ins. Applied`\| + \|`Payment: Net Patient/Other Applied`\| across lines | absolute value |
| `claim.contractual_adjustment` | sum of \|`Adjustment: Net Primary Ins. Adjusted`\| across lines | |
| `claim.other_adjustment` | sum of \|`Non-Primary Adj`\| + \|`Patient/Other Adj`\| across lines | |
| `claim.patient_responsibility` | sum of `Charge Balance: Patient` across lines | |
| `claim.balance` | computed — `recompute_balance(claim)` | Phase 2a utility |
| `service_line.billed_amount` | `Charge: Gross Charges` | **not** `Charge: Charge Amount` (per-unit) |
| `service_line.units` | `Charge: Net Units` | |
| `service_line.procedure_code` | `Procedure: Code` | |
| `service_line.modifier_1..4` | split `Procedure: Modifiers` on whitespace/comma | >4 → warning, extras dropped |
| `service_line.date_of_service_from` | `Date: Service date of the Charge` | |
| `service_line.paid_amount` | \|`Payment: Net Primary Ins. Applied`\| | |
| `service_line.patient_responsibility` | `Charge Balance: Patient` | |
| `service_line.contractual_adjustment` | \|`Adjustment: Net Primary Ins. Adjusted`\| | |
| `service_line.other_adjustment` | \|Non-Primary Adj\| + \|Patient/Other Adj\| | |
| `service_line.diagnosis_codes` | `[Diagnosis: Primary ICD-10 Code]` | one-item list |
| Patient auto-create fields | `Patient: First Name`, `Last Name`, `Date Of Birth`, `Sex`, `Phone Primary`, `Address Line 1/2`, `City`, `State`, `Zip Code` | only used when no match |

### Rows excluded before mapping

- `Charge: Void Indicator == "YES"` → counted in `skipped_voids`, dropped.
- Missing `Visit: VisitID` → error issue, row dropped.
- Non-numeric `Charge: Gross Charges` → error issue, row dropped.

### `Diagnosis: Primary Code` column

Ignored (ICD-9, float artifacts like `625.9000000000001`). We only use `Diagnosis: Primary ICD-10 Code`.

## Backend

### One-time wipe: `backend/scripts/reset_claims_data.py`

Guarded by `--yes-i-am-sure` flag. Deletes in leaf-first order across `service_line_adjustments`, `claim_adjustments`, `service_lines`, `appeals`, `denials`, `claims`, `era_files`. Then filtered `audit_log.delete()` WHERE `resource_type in ('claim', 'service_line', 'claim_adjustment', 'service_line_adjustment', 'denial', 'appeal', 'era_file', 'charge_analysis_file')`. Returns a dict of `{table_name: rows_deleted}` for the caller to print.

Does NOT touch: `patients`, `users`, `user_groups`, `documents`, `fax_*`, audit rows with other resource types.

Idempotent: second run deletes 0 rows. Touches data only, never the schema. Safe to keep in the repo after it's been used — the `--yes-i-am-sure` flag prevents accidental future runs.

### New service: `backend/app/services/charge_analysis_importer.py`

Pure parser, no DB, no FastAPI. Entry point: `parse(path: str) -> ChargeAnalysisImport`.

Dataclasses:
```python
@dataclass
class ParsedServiceLine:
    procedure_code: Optional[str]
    modifier_1: Optional[str]
    modifier_2: Optional[str]
    modifier_3: Optional[str]
    modifier_4: Optional[str]
    units: Decimal
    billed_amount: Decimal           # Charge: Gross Charges
    paid_amount: Decimal
    patient_responsibility: Decimal
    contractual_adjustment: Decimal
    other_adjustment: Decimal
    date_of_service_from: Optional[date]
    diagnosis_codes: List[str]

@dataclass
class ParsedClaim:
    visit_id: str                    # claim_number anchor
    patient_external_id: str         # PrimeSuite Patient ID
    patient_demographics: Dict[str, Any]
    date_of_service_from: Optional[date]
    payer_name: Optional[str]
    subscriber_id: Optional[str]
    secondary_payer_name: Optional[str]
    secondary_subscriber_id: Optional[str]
    rendering_provider_name: Optional[str]
    rendering_provider_npi: Optional[str]
    billing_provider_npi: Optional[str]
    # Rollups across service_lines
    billed_amount: Decimal
    paid_amount: Decimal
    patient_responsibility: Decimal
    contractual_adjustment: Decimal
    other_adjustment: Decimal
    service_lines: List[ParsedServiceLine]

@dataclass
class ParseIssue:
    severity: Literal["error", "warning"]
    row_index: int
    visit_id: Optional[str]
    message: str

@dataclass
class ChargeAnalysisImport:
    claims: List[ParsedClaim]
    skipped_voids: int
    issues: List[ParseIssue]
    source_filename: str
    total_rows: int
```

Parse steps:
1. `pd.read_excel(path, sheet_name=0)`.
2. Validate required columns are present; missing → raise with the list.
3. Filter `Void Indicator == "YES"` rows; count.
4. Normalize dates, money (Decimal via `str(v)`), trim whitespace.
5. Split `Procedure: Modifiers` on whitespace/comma; keep first 4, warn on >4.
6. Build one `ParsedServiceLine` per data row.
7. Group by `Visit: VisitID`. First row's claim-level fields win; warn if they diverge. Sum money fields across lines into claim-level rollups.
8. Return `ChargeAnalysisImport`.

Always uses `abs()` on `Payment: Net Primary Ins. Applied` and `Payment: Net Patient/Other Applied` (PrimeSuite stores as negative). Also on `Adjustment: Net Primary Ins. Adjusted` etc.

### New session store: `backend/app/services/import_sessions.py`

Module-level `_sessions: Dict[str, SessionEntry]`. Interface:
```python
def put(session_id: str, entry: SessionEntry) -> None
def get(session_id: str) -> Optional[SessionEntry]   # lazily expires stale entries
def purge(session_id: str) -> None
def expire_old() -> int                               # optional cleanup helper
```

`SessionEntry` carries: parsed `ChargeAnalysisImport`, `created_at`, `expires_at`, per-claim pre-computed flags (`exists_in_db: bool`, `patient_resolved_id: Optional[str]`, `will_create_patient: bool`), `file_path`, `user_email`.

TTL: 30 min. Not persisted, not multi-worker safe — noted in module docstring with Redis-swap TODO.

### New router: `backend/app/routers/charge_imports.py`

Registered in `main.py` with `dependencies=BILLING`. Prefix `/imports`.

#### `POST /api/imports/charge-analysis`

Body: multipart/form-data with `file`.

Steps:
1. Generate `session_id = uuid4()`.
2. Save upload to `{settings.upload_dir}/charge_analysis/{session_id}.xls`.
3. Parse via `charge_analysis_importer.parse()`. Parser raises on malformed file → 422 with detail.
4. For each parsed claim, query `Claim.claim_number == visit_id` → set `exists_in_db`.
5. For each parsed claim, query `Patient.patient_id == patient_external_id` → set `patient_resolved_id` and `will_create_patient = not match`.
6. Put entry in session store.
7. Return preview JSON:

```json
{
  "session_id": "...",
  "source_filename": "Charge Analysis Test4.xls",
  "total_rows": 1717,
  "parsed_claims": 759,
  "skipped_voids": 12,
  "will_create": 512,
  "will_skip_existing": 247,
  "will_create_patients": 23,
  "will_match_patients": 736,
  "errors": 2,
  "warnings": 8,
  "sample_claims": [ /* first 20 ParsedClaim dicts */ ],
  "issues": [ /* full list of ParseIssue */ ],
  "expires_at": "2026-04-20T17:05:00Z"
}
```

Error cases:
- File can't be read by pandas → 422 `"could not read Excel file"`.
- Missing required columns → 422 with `missing_columns` list.
- Any other raise → 500, file remains on disk for debugging, session NOT created.

#### `POST /api/imports/charge-analysis/{session_id}/commit`

Body: empty.

Steps:
1. `get(session_id)` → 404 if missing or expired.
2. For each parsed claim NOT flagged `exists_in_db`:
   - **Patient**: if `patient_resolved_id` set, use it. Otherwise create `Patient(patient_id=patient_external_id, **demographics)`.
   - **Claim**: insert with `claim_number = visit_id`, all mapped fields, `status = PENDING`, `insurance_order = PRIMARY`.
   - **Service lines**: bulk insert one per `ParsedServiceLine`, each linked to the new claim.
   - `recompute_balance(claim)` from Phase 2a.
   - Commit this claim's transaction.
   - Write `log_action("CREATE", "claim", resource_id=str(claim.id), user_name=..., patient_id=str(claim.patient_id), new_values={summary of claim fields})`.
3. If a claim insert fails, record the failure in an `errors` array and continue with the next claim (per-claim transaction isolation).
4. After the loop, write one `log_action("IMPORT", "charge_analysis_file", resource_id=session_id, user_name=..., description=f"{filename} — {created} created, {skipped} skipped, {patients_created} patients created")`.
5. Purge session from store. Keep file on disk.
6. Return:

```json
{
  "source_filename": "Charge Analysis Test4.xls",
  "claims_created": 512,
  "claims_skipped_existing": 247,
  "patients_created": 23,
  "patients_matched": 736,
  "service_lines_created": 1154,
  "errors": [ { "visit_id": "...", "message": "..." } ]
}
```

### main.py wiring

```python
from app.routers import (
    imports, claims, patients, denials, appeals, eob, audit,
    waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch,
    admin_users, service_lines, claim_adjustments, service_line_adjustments,
    charge_imports,  # NEW
)
...
app.include_router(charge_imports.router, prefix="/api", dependencies=BILLING)
```

### Model changes

None. All target tables exist from Phase 2a.

## Frontend

### `ImportFiles.jsx` — new card below existing ERA upload

Uses the existing drop-zone + state pattern. Local component state:

```js
const [chargeUpload, setChargeUpload] = useState(null)
// states:
// null                                 → show drop zone
// { uploading: true, filename }        → show spinner
// { preview: {...} }                   → show preview card
// { preview, committing: true }        → preview + disabled buttons + spinner on Commit
// { success: {...} }                   → success card
// { error: "...", preview? }           → error card with Retry / Dismiss
```

A `useEffect` + `setInterval(1000)` drives the session-expiry countdown from `preview.expires_at`. Hits 0 → card switches to "Session expired. Please re-upload." and disables Commit.

### Issues disclosure

Preview card has a collapsible "Show details" that expands a scrollable list of all parse issues (`severity | row# | VisitID | message`). Shown with severity coloring (red for errors, amber for warnings).

### Styling

Matches existing `plum-50 / plum-600 / card / btn-primary / btn-secondary / text-muted / text-ink` utility classes in `frontend/src/index.css`. Follows the aesthetic set by Phase 2a drawer components.

### API calls

No new axios instance; uses existing `api` from `utils/api.js`:
- `api.post('/imports/charge-analysis', formData, {headers: {'Content-Type': 'multipart/form-data'}})`
- `api.post(\`/imports/charge-analysis/\${session_id}/commit\`)`

Both POSTs invalidate `['claims']` React Query key on success so the Claims list reflects the new rows.

### Access control

Already enforced at the router level (BILLING). The page `/imports` is gated in the existing routing; clinical users redirected. No per-component auth work.

## Files touched

**Backend — created:**
- `backend/scripts/reset_claims_data.py`
- `backend/app/services/charge_analysis_importer.py`
- `backend/app/services/import_sessions.py`
- `backend/app/routers/charge_imports.py`
- `backend/tests/fixtures/charge_analysis_test4.xls`
- `backend/tests/test_charge_analysis_parser.py`
- `backend/tests/test_charge_imports_router.py`
- `backend/tests/test_reset_claims_data.py`

**Backend — modified:**
- `backend/app/main.py` — include `charge_imports.router` with BILLING guard

**Frontend — modified:**
- `frontend/src/pages/ImportFiles.jsx` — new Charge Analysis card + preview + commit flow

**Frontend — unchanged** but used:
- `frontend/src/utils/api.js` — existing `api` instance

**Scripts — one-time execution:**
- Production: `cd backend && source venv/bin/activate && python -m app.scripts.reset_claims_data --yes-i-am-sure`

## Verification

### Automated (pytest)

Run `pytest backend/tests/` — all existing tests (123 from Phase 2a) plus 30 new tests across parser / router / wipe-script must pass.

### Manual UI checklist

- [ ] Wipe ran cleanly and returned expected counts.
- [ ] `/imports` shows the new Charge Analysis card below the ERA zone.
- [ ] Drop `Charge Analysis Test4.xls` → spinner → preview card with correct counts (matches pytest assertions).
- [ ] Click Show details → issues scroll, severity colors correct.
- [ ] Session expiry timer counts down; near 0:00 Commit disables.
- [ ] Cancel returns to drop zone.
- [ ] Commit shows spinner, returns success card ~2-5s later, stats match preview counts.
- [ ] `/claims` list shows the new 512 claims.
- [ ] Open a claim from the import → ClaimDetail shows service lines, computed balance, Phase 2a edit drawer works.
- [ ] Re-upload the same file → preview shows `will_skip_existing == 512`, `will_create == 0`. Commit no-ops.
- [ ] Upload a random PDF or CSV → 422 error banner, no session created.
- [ ] Upload as clinical user (flip group in sqlite) → /imports route redirects, endpoint 403s.

### Fixture privacy

`backend/tests/fixtures/charge_analysis_test4.xls` contains real patient data. Acceptable at this stage (private repo, single-user project). If/when the repo opens up, add a fixture-sanitization script.

## Tests (backend)

### `test_charge_analysis_parser.py`

1. `test_parse_real_fixture_file` — 759 claims, correct skip counts, zero errors on the clean fixture.
2. `test_parse_single_line_claim` — synthetic 1-row DataFrame, 1 claim + 1 SL.
3. `test_parse_multi_line_claim` — 3-row group, 1 claim + 3 SL, money rollups summed.
4. `test_parse_multi_unit_service_line` — J2003 units=20, charge=$1.50 → SL billed=$30.
5. `test_parse_voided_row_skipped` — Void=YES not in output, counted in `skipped_voids`.
6. `test_parse_payment_negative_sign_normalized` — `-119.75` → `paid_amount=119.75`.
7. `test_parse_modifier_splitting` — `"25 59"` / `"25 59 76 RT LT"` cases.
8. `test_parse_missing_visit_id_row_dropped` — row dropped, error issue recorded.
9. `test_parse_non_numeric_charge_amount_rejected` — error issue, row dropped.
10. `test_parse_missing_required_column_raises` — ValueError listing missing columns.
11. `test_parse_payer_name_differs_across_lines_warns` — warning, first line's value kept.
12. `test_parse_negative_gross_charge_warns_but_parses` — claim parsed, warning emitted.

### `test_charge_imports_router.py`

13. `test_upload_returns_preview` — POST fixture, 200, all preview fields populated, `sample_claims` length=20.
14. `test_upload_detects_existing_claim_number` — pre-seeded claim → `will_skip_existing >= 1`.
15. `test_upload_detects_matching_patient` — pre-seeded patient → `will_match_patients >= 1`.
16. `test_commit_creates_claims_and_service_lines` — counts match.
17. `test_commit_skips_existing_by_visit_id` — pre-seeded claim unchanged after commit.
18. `test_commit_creates_missing_patients` — new Patient rows with file demographics.
19. `test_commit_does_not_duplicate_existing_patients` — matching chart id, no duplicate.
20. `test_commit_recomputes_claim_balance` — balance math for a 2-line claim.
21. `test_commit_writes_audit_row_per_claim` — CREATE rows with user_name + patient_id.
22. `test_commit_writes_single_import_audit_row` — one IMPORT row with resource_type `charge_analysis_file`.
23. `test_commit_per_claim_failure_isolated` — monkeypatch one claim to raise; others succeed; failed listed in response.
24. `test_commit_404_on_unknown_session`.
25. `test_commit_404_on_expired_session` — advance fake clock past TTL.
26. `test_upload_forbidden_for_clinical` — `clinical_client` → 403.
27. `test_commit_forbidden_for_clinical` — `clinical_client` → 403.

### `test_reset_claims_data.py`

28. `test_wipe_deletes_claim_side_data_preserves_others` — seeds one of each table; after `run(True)`, claim-side all 0; patient/user/document untouched; audit rows with wiped types removed; audit rows with other types preserved.
29. `test_wipe_refuses_without_confirm_flag` — `run(False)` raises SystemExit; no deletes.
30. `test_wipe_is_idempotent` — second `run(True)` returns all-zero counts, no errors.

## Open questions

None blocking.
