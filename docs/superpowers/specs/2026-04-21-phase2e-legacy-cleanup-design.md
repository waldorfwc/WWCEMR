# Phase 2e — Legacy Cleanup

**Date:** 2026-04-21
**Project:** wwc-era-project
**Depends on:** Phase 2c (ERA posting shipped `process` logic in `era_poster.py`), Phase 2d (Claims Analysis enrichment shipped).
**Blocks:** none — this is a housekeeping phase that finishes retiring the pre-2b ingest path.

## Goal

Remove all references to the pre-Phase-2b generic ingest path. Delete `era_import_service.py`, `file_importer.py`, and the legacy `POST /api/imports/upload` endpoint + its UI drop zone. Move the helpers still in use into `era_poster.py`. Extract the reusable ERA-posting body into a shared `process_era_file(db, content, filename, user_email)` helper used by both the `era_posting` commit endpoint and the Waystar SFTP sync. Prune the ~1,955 orphan Payment rows left over from Phase 2b's wipe. Net result: one and only one ERA ingest pipeline (`/api/imports/era-posting`) used by both UI and SFTP.

## Workflow

(No user-facing workflow change. This is a refactor + cleanup.)

1. Existing `/imports` page loses its generic drop zone and amber banner.
2. Existing 3 upload cards (Claims Analysis, ERA 835 Posting, Charge Analysis) are unchanged.
3. Existing ERA file history at the bottom of `/imports` is unchanged.
4. Waystar SFTP sync endpoint, previously broken since Phase 2c, now actually posts payments again (uses the new pipeline internally).
5. Operator runs `python -m app.scripts.prune_orphan_payments --yes-i-am-sure` once post-merge to clean the Payments table.

## Non-goals

1. No behavior changes to `/api/imports/era-posting`, `/api/imports/claim-id-bootstrap`, `/api/imports/charge-analysis`. The three active upload flows are frozen.
2. No schema changes.
3. No new UI.
4. No route renames. `GET /api/imports/era-files` history endpoint stays at the same URL.
5. No changes to `era_835.py` parser.
6. No changes to denial-analysis logic (`_create_denials`, `analyze_denial`, `maryland_rules`). These move locations but are unmodified.
7. No changes to ERA posting behavior — status mapping, CO-45 skip, reversal flagging, payment dedup, denial creation — all identical to Phase 2c.
8. No follow-up queue page (per user's explicit scope note).
9. No removal of `era_file_path` field on `EraFile` model. The helper sets it to `""` when the caller has no path; otherwise caller passes the path.
10. No real-SFTP integration test. Waystar test mocks the download step.
11. No changes to `log_action("IMPORT", "era_file", ...)` audit row format.
12. No removal of the `Payment` model or table.

## Architecture

No new models, no new routes, no new UI. Three code-motion operations plus two deletions plus one cleanup script:

1. **Code motion (no behavior change):** move `SKIP_DENIAL_CODES`, `CONTRACTUAL_CODES`, `_determine_claim_status`, `_has_real_denials`, `_create_denials` from `backend/app/services/era_import_service.py` into `backend/app/services/era_poster.py` (above the existing `post_claim` function).

2. **Code extraction:** add a new public helper `process_era_file(db, content, filename, user_email) -> ProcessResult` to `era_poster.py`. It parses via `Era835Parser`, calls `build_preview`, creates the `EraFile` row, loops over `preview.matches` posting each `matched` claim via `post_claim`, writes the per-file `IMPORT` audit row, and returns structured counts.

3. **Refactor:** `era_posting.commit_eras` stops inlining its per-preview loop and delegates to `process_era_file(db, content, filename, user_email)` per file, re-reading content from each preview's saved `_file_path`. Aggregates counts across files. Response shape unchanged.

4. **Rewire:** `waystar.sync_eras_sftp` replaces its broken `import_file(...)` + `import_era_file(...)` calls with `process_era_file(db, content, os.path.basename(fpath), user_email="waystar-sftp-sync")` per downloaded file. Restores working behavior (endpoint has been throwing `NotImplementedError` since Phase 2c).

5. **Deletion:** remove these entirely:
   - `backend/app/services/era_import_service.py` (all remaining content now lives in `era_poster.py`).
   - `backend/app/parsers/file_importer.py` (no remaining callers).
   - `upload_file` function + its imports from `backend/app/routers/imports.py`. Keep the router definition and the `list_era_files` endpoint.
   - `backend/tests/test_legacy_era_disabled.py` — all 3 tests become meaningless.

6. **Data cleanup:** new one-time script `backend/app/scripts/prune_orphan_payments.py` runs `DELETE FROM payments WHERE claim_id IS NOT NULL AND claim_id NOT IN (SELECT id FROM claims)`. Idempotent. Guarded by `--yes-i-am-sure` flag.

7. **Frontend:** strip the legacy drop zone + `{result}` / `{error}` result cards + the Phase 2c amber warning banner from `frontend/src/pages/ImportFiles.jsx`. Remove `dragging`/`uploading`/`result`/`error`/`inputRef` state, `handleFile`/`onDrop`/`formatIcon` functions, and the `Upload` icon import. Keep all 3 active upload cards and the ERA history section. Update page header copy to describe the 3 remaining flows.

## Backend details

### `era_poster.py` — additions

Move verbatim from `era_import_service.py` (above existing `post_claim`):

- `SKIP_DENIAL_CODES = {"45"}`
- `CONTRACTUAL_CODES = {"45", "44", "23", "24", "36"}`
- `_determine_claim_status(era_claim) -> ClaimStatus`
- `_has_real_denials(era_claim) -> bool`
- `_create_denials(db, claim, era_claim, era)` — includes its local imports (`get_carc_info`, `analyze_denial`, `get_payer_rules`, `DenialStatus`, `DenialCategory` alias).

Remove the `from app.services.era_import_service import (...)` block — helpers are now local.

Add `ProcessResult` dataclass and `process_era_file` function:

```python
@dataclass
class ProcessResult:
    era_file_id: Optional[str]
    claims_posted: int
    claims_already_posted: int
    claims_unmatched: int
    claims_reversal_flagged: int
    claims_cb_skipped: int
    claims_malformed: int
    payments_created: int
    denials_created: int
    errors: List[Dict[str, Any]]
    parse_errors: List[str]


def process_era_file(
    db: Session,
    content: str,
    filename: str,
    user_email: Optional[str],
) -> ProcessResult:
    """Parse + match + post one ERA file end-to-end. No session/preview state.

    Callers: era_posting.commit (per EraFilePreview), waystar.sync_eras_sftp
    (per downloaded file). Creates an EraFile DB row, posts each matched
    claim in its own transaction, returns counts + errors.
    """
    from app.parsers.era_835 import Era835Parser
    from app.models.claim import EraFile as EraFileModel
    from app.models.denial import Denial

    try:
        era = Era835Parser().parse(content, filename=filename)
    except Exception as exc:
        return ProcessResult(
            era_file_id=None, claims_posted=0, claims_already_posted=0,
            claims_unmatched=0, claims_reversal_flagged=0, claims_cb_skipped=0,
            claims_malformed=0, payments_created=0, denials_created=0,
            errors=[{"filename": filename,
                     "message": f"parse failed: {type(exc).__name__}: {exc}"}],
            parse_errors=[],
        )

    preview = build_preview(db, era, source_filename=filename)

    era_file_row = EraFileModel(
        filename=filename, file_path="",
        payer_name=era.payer_name, payer_id=era.payer_id,
        check_number=era.check_number, check_date=era.check_date,
        check_amount=era.check_amount,
        transaction_count=len(era.claims),
        status="processed" if not era.parse_errors else "partial",
        error_log="\n".join(era.parse_errors) if era.parse_errors else None,
        imported_by=user_email or "era-poster",
    )
    db.add(era_file_row); db.commit(); db.refresh(era_file_row)

    denials_before = db.query(Denial).count()

    claims_posted = payments_created = 0
    claims_already_posted = claims_unmatched = 0
    claims_reversal_flagged = claims_cb_skipped = claims_malformed = 0
    errors: List[Dict[str, Any]] = []

    for m in preview.matches:
        if m.status == "matched":
            try:
                post_claim(db, m, era, era_file_row, user_email=user_email)
                claims_posted += 1
                payments_created += 1
            except Exception as exc:
                db.rollback()
                errors.append({"internal_claim_id": m.internal_claim_id,
                               "message": f"{type(exc).__name__}: {exc}"})
        elif m.status == "already_posted":   claims_already_posted += 1
        elif m.status == "unmatched":         claims_unmatched += 1
        elif m.status == "reversal_flagged":  claims_reversal_flagged += 1
        elif m.status == "cb_prefix_skipped": claims_cb_skipped += 1
        elif m.status == "malformed_clp01":   claims_malformed += 1

    denials_created = db.query(Denial).count() - denials_before

    log_action(db, "IMPORT", "era_file",
               resource_id=str(era_file_row.id), user_name=user_email,
               description=f"{filename} — {claims_posted} posted, {claims_unmatched} unmatched")

    return ProcessResult(
        era_file_id=str(era_file_row.id),
        claims_posted=claims_posted,
        claims_already_posted=claims_already_posted,
        claims_unmatched=claims_unmatched,
        claims_reversal_flagged=claims_reversal_flagged,
        claims_cb_skipped=claims_cb_skipped,
        claims_malformed=claims_malformed,
        payments_created=payments_created,
        denials_created=denials_created,
        errors=errors,
        parse_errors=list(era.parse_errors or []),
    )
```

### `era_posting.commit_eras` — refactor

Replace the current per-preview loop body with a call to `process_era_file`. Re-read content from the saved file path (`p.__dict__["_file_path"]`). Aggregate counts. Response shape unchanged.

### `waystar.sync_eras_sftp` — rewire

Replace the broken `import_file(...)` + `import_era_file(...)` calls with `process_era_file(db, content, filename=os.path.basename(fpath), user_email="waystar-sftp-sync")`. Each downloaded file contributes one EraFile row and as many Payment rows as it matches. Errors per file are collected in the response.

### `imports.py` — delete `upload_file`

Delete the `upload_file(...)` function and its imports (`from app.parsers.file_importer import import_file`, `from app.services.era_import_service import import_era_file`). Keep the router definition and `list_era_files` (`GET /era-files`).

### File deletions

```bash
rm backend/app/services/era_import_service.py
rm backend/app/parsers/file_importer.py
rm backend/tests/test_legacy_era_disabled.py
```

### `prune_orphan_payments.py` — new script

```python
"""One-time migration — delete Payment rows whose claim_id no longer resolves.

Caused by Phase 2b's claim wipe which didn't touch the payments table.

Usage (from backend/):
    source venv/bin/activate
    python -m app.scripts.prune_orphan_payments --yes-i-am-sure
"""
import argparse
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import SessionLocal


def run(confirm: bool, session: Optional[Session] = None) -> int:
    if not confirm:
        raise SystemExit("Refusing to run without --yes-i-am-sure flag.")
    db = session if session is not None else SessionLocal()
    owns_db = session is None
    try:
        result = db.execute(text(
            "DELETE FROM payments WHERE claim_id IS NOT NULL AND "
            "claim_id NOT IN (SELECT id FROM claims)"
        ))
        deleted = result.rowcount
        db.commit()
    finally:
        if owns_db:
            db.close()
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes-i-am-sure", action="store_true")
    args = parser.parse_args()
    deleted = run(confirm=args.yes_i_am_sure)
    print(f"Pruned {deleted} orphan Payment rows.")


if __name__ == "__main__":
    main()
```

## Frontend details

### `ImportFiles.jsx` — removals

**State hooks to delete:**
- `const [dragging, setDragging] = useState(false)`
- `const [uploading, setUploading] = useState(false)`
- `const [result, setResult] = useState(null)`
- `const [error, setError] = useState(null)`
- `const inputRef = useRef()`

**Functions to delete:**
- `handleFile(file)`
- `onDrop(e)`
- `formatIcon(fmt)`

**JSX to delete:**
- Generic drop zone (around lines 55-86).
- `{result && ...}` result card (around lines 89-156).
- `{error && ...}` error card (around lines 158-166).
- Phase 2c amber warning banner.

**Imports to clean:**
- Remove `Upload` from the `lucide-react` import line (keep `FileText, CheckCircle, AlertCircle, Clock, Database, Link2`).

**Page header — replace:**

```jsx
<h1 className="text-2xl font-bold text-gray-900 mb-2">Import Files</h1>
<p className="text-gray-500 text-sm mb-6">
  Supported: ERA 835 (X12 EDI), CSV, XLS/XLSX, PDF · ERA files are auto-imported · Others show a preview for review
</p>
```

With:

```jsx
<h1 className="text-2xl font-bold text-gray-900 mb-2">Import</h1>
<p className="text-gray-500 text-sm mb-6">
  Three upload flows below: <strong>Charge Analysis</strong> creates claims from PrimeSuite,
  <strong>Claims Analysis</strong> links Claim IDs + workflow fields, <strong>ERA 835</strong>
  posts payments. Uploaded file history is at the bottom.
</p>
```

### What stays

- Claims Analysis Import card (Phase 2c/2d).
- ERA 835 Payment Posting card (Phase 2c).
- Charge Analysis Import card (Phase 2b).
- ERA File Import History card (`GET /api/imports/era-files`).
- All React Query hooks except the `useQuery(['era-files'])` hook, which still feeds the history card.

## Testing

### Backend

**Delete** `backend/tests/test_legacy_era_disabled.py` — 3 tests become meaningless (module is gone; 410 endpoint is gone; helpers now live in `era_poster`). `−3 tests`.

**Create** `backend/tests/test_prune_orphan_payments.py` — 2 tests:
1. `test_prune_deletes_only_orphans` — seed a valid payment + an orphan payment; `run(confirm=True)` returns `1`; remaining payment ties to the valid claim.
2. `test_prune_refuses_without_confirm_flag` — `run(confirm=False)` raises `SystemExit`; DB untouched.

**Create** `backend/tests/test_waystar_sync_uses_era_poster.py` — 1 integration test:
3. `test_sync_eras_sftp_posts_matched_claims` — seed a matching Claim; patch `get_waystar_client` to return a stub that returns a local path to the real `johns_hopkins_era.835` fixture; POST `/api/waystar/sync-eras-sftp`; assert `claims_posted >= 1`, `EraFileModel` row created, `Payment` row created.

**Net test delta:** −3 + 2 + 1 = 0. Full suite stays at **241**.

### Frontend

No RTL harness. Smoke-verify `npx vite build` per commit. Manual checklist in the final task:
- `/imports` page: "Import" header + 3-flow subtitle; no generic drop zone; no amber banner; 3 upload cards + history.
- Uploading a random `.pdf` into any specific card still correctly rejects at the endpoint level (422).
- Waystar SFTP sync endpoint (if reachable to a real SFTP) now actually posts; test covers the logic end-to-end with a mocked download step.

### Manual migration post-merge

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend
source venv/bin/activate
python -m app.scripts.prune_orphan_payments --yes-i-am-sure
# expected output: "Pruned 1955 orphan Payment rows."
```

Re-run → `Pruned 0 orphan Payment rows.` (idempotent).

## Files touched

**Backend — created:**
- `backend/app/scripts/prune_orphan_payments.py`
- `backend/tests/test_prune_orphan_payments.py`
- `backend/tests/test_waystar_sync_uses_era_poster.py`

**Backend — modified:**
- `backend/app/services/era_poster.py` — add moved helpers (verbatim) + `ProcessResult` + `process_era_file`; remove the `from app.services.era_import_service import ...` block.
- `backend/app/routers/era_posting.py` — `commit_eras` now delegates to `process_era_file`. Response shape unchanged.
- `backend/app/routers/waystar.py` — `sync_eras_sftp` uses `process_era_file`. Broken endpoint now works.
- `backend/app/routers/imports.py` — delete `upload_file` function + file-importer imports. Keep `list_era_files`.

**Backend — deleted:**
- `backend/app/services/era_import_service.py`
- `backend/app/parsers/file_importer.py`
- `backend/tests/test_legacy_era_disabled.py`

**Frontend — modified:**
- `frontend/src/pages/ImportFiles.jsx` — delete legacy drop zone + state + handlers + amber banner; update page header; clean icon imports.

**One-time execution post-merge:**
- `python -m app.scripts.prune_orphan_payments --yes-i-am-sure`

## Verification

### Automated

```bash
cd backend && source venv/bin/activate && python -m pytest tests/ 2>&1 | tail -5
```
Expected: **241 passed** (unchanged from Phase 2d — we deleted 3 and added 3).

### Grep checks

```bash
grep -r "era_import_service" backend/ --include="*.py"
grep -r "file_importer" backend/ --include="*.py"
grep -rn "/imports/upload" backend/ frontend/src/
```
Expected: **all zero hits.**

### Manual UI

- `/imports` page renders: Import header + 3-flow subtitle + 3 active cards + ERA history. No generic drop zone. No amber banner.
- Open any claim via `/claims/:id` → edit drawer still has Workflow section (Phase 2d unchanged).

### Manual data cleanup

After merge:
```bash
python -m app.scripts.prune_orphan_payments --yes-i-am-sure
```
Expected: prints non-zero prune count first time, `0` on re-run.

## Tests (backend)

### `test_prune_orphan_payments.py` (2 tests)
1. `test_prune_deletes_only_orphans` — seed valid + orphan; run; only orphan deleted.
2. `test_prune_refuses_without_confirm_flag` — `run(confirm=False)` → SystemExit; DB untouched.

### `test_waystar_sync_uses_era_poster.py` (1 test)
3. `test_sync_eras_sftp_posts_matched_claims` — mock SFTP download, real ERA fixture, verify `claims_posted`, `EraFile` row, `Payment` row.

### Deleted
- `backend/tests/test_legacy_era_disabled.py` — 3 tests (module removed; endpoint removed; helpers relocated).

**Net delta: 0 tests.** Full suite: **241**.

## Open questions

None blocking.
