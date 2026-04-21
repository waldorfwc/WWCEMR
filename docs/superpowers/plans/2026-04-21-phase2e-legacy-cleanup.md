# Phase 2e — Legacy Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the pre-Phase-2b ingest path (`era_import_service.py`, `file_importer.py`, `/api/imports/upload`, and the generic drop zone UI), move reused helpers into `era_poster.py`, extract a shared `process_era_file` helper used by both `era_posting` commit and the Waystar SFTP sync, and prune orphan Payment rows.

**Architecture:** Incremental refactor. T1 adds new code without breaking callers. T2–T3 switch call sites to the new helper. T4–T5 delete the now-dead legacy code. T6 adds the one-time cleanup script. T7 strips the frontend drop zone. Net-zero test delta (−3 from deleted legacy test file, +3 new tests).

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend); React 18 + Vite + Tailwind + React Query v5 (frontend).

**Reference spec:** `docs/superpowers/specs/2026-04-21-phase2e-legacy-cleanup-design.md`

---

## Pre-flight notes

- Branch `phase-2e-legacy-cleanup`, head `cece0c0` (spec commit). Clean tree.
- Baseline test count: **241**. After all tasks: **241** (net zero: +3 new, −3 deleted).
- Git identity override on every commit:
  `git -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" commit -m "..."`
- **DO NOT touch** (frozen for this phase): `era_835.py`, `claim_math.py`, `claims_analysis_matcher.py`, `claim_id_bootstrap.py`, `charge_analysis_importer.py`, `charge_imports.py`, `claims.py`, `admin_users.py`, service-line routers.
- Response shape for `/api/imports/era-posting/{session_id}/commit` must stay identical — only the internal loop is refactored.
- Response shape for `/api/waystar/sync-eras-sftp` changes slightly: the per-file dict now has `claims_posted`, `claims_unmatched`, `status` (in place of `claims`, `status`). This is acceptable breaking change because the endpoint has been throwing `NotImplementedError` since Phase 2c — nothing consumes the old shape successfully.
- `log_action("IMPORT", "era_file", ...)` audit row format unchanged.

---

## Task 1: Backend — add helpers + `process_era_file` to `era_poster.py`

Moves the 5 helpers from `era_import_service.py` into `era_poster.py` and adds the new public helper. No existing tests change — existing callers of `era_poster` (from Phase 2c T6/T7) are unaffected because we don't break any function signature. The `from app.services.era_import_service import ...` block stays for now (removed in T2 after we verify `commit_eras` works via the new helper).

**Files:**
- Modify: `backend/app/services/era_poster.py`

- [ ] **Step 1: Run baseline tests to confirm starting state**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ 2>&1 | tail -3
```
Expected: **241 passed**.

- [ ] **Step 2: Read the helpers to be moved from `era_import_service.py`**

Read `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/era_import_service.py`. Locate:
- `SKIP_DENIAL_CODES = {"45"}` constant
- `CONTRACTUAL_CODES = {"45", "44", "23", "24", "36"}` constant
- `_determine_claim_status(era_claim) -> ClaimStatus` (maps CLP02 code to ClaimStatus enum)
- `_has_real_denials(era_claim) -> bool` (detects non-contractual denials)
- `_create_denials(db, claim, era_claim, era)` (creates Denial rows; imports `get_carc_info`, `analyze_denial`, `get_payer_rules`, `DenialStatus`, `DenialCategory`)

Copy each verbatim including docstrings.

- [ ] **Step 3: Add helpers to `era_poster.py`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/era_poster.py`.

Find the existing `from app.services.era_import_service import (...)` block (around line 143). Immediately ABOVE it, paste the 5 helpers verbatim from `era_import_service.py`. Include their local imports — `_create_denials` needs:

```python
from app.utils.carc_codes import get_carc_info
from app.utils.maryland_rules import get_payer_rules
from app.services.denial_analyzer import analyze_denial
from app.models.denial import Denial, DenialStatus
from app.models.denial import DenialCategory as ModelDenialCategory
```

(If the plan has `CarcDenialCategory` alias, use it per the source.)

- [ ] **Step 4: Delete the `era_import_service` import block from `era_poster.py`**

Delete this block from `era_poster.py`:
```python
from app.services.era_import_service import (
    _determine_claim_status, _has_real_denials, _create_denials,
    SKIP_DENIAL_CODES,
)
```

The local copies added in Step 3 now satisfy all in-file references. The `era_import_service.py` file still exists and still imports cleanly — we delete it entirely in T5 once we've confirmed nothing in the tree references it.

- [ ] **Step 5: Add `ProcessResult` dataclass + `process_era_file` function**

At the BOTTOM of `era_poster.py` (after all existing functions), append:

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
    """Parse + match + post one ERA file end-to-end.

    Callers: era_posting.commit (per EraFilePreview), waystar.sync_eras_sftp
    (per downloaded file). Creates an EraFile DB row, posts each matched
    claim in its own transaction, returns structured counts + errors.
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

Note: `@dataclass` may need `from dataclasses import dataclass` if not already imported; `Dict`, `Any`, `List`, `Optional` may need `from typing import ...`. Check the top of the file and add missing imports.

- [ ] **Step 6: Run tests to verify no regressions**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ 2>&1 | tail -3
```
Expected: **241 passed**. If `tests/test_legacy_era_disabled.py` fails its "helpers still importable" test because we just moved the helpers out of `era_import_service.py`, that's expected — the test will be deleted in T4. Skip running the suite, or expect 1 failure here.

If you see 1 failure in `test_legacy_era_disabled.py::test_legacy_helpers_still_importable`, that's fine — continue to the next step.

Actually, check first: the test imports the helpers FROM `era_import_service` directly. Since we didn't delete `era_import_service.py` yet (just removed the import in `era_poster.py`), the helpers STILL exist in `era_import_service.py` — the test should still pass. The test was checking the helpers are reachable; they still are (just not consumed by era_poster anymore).

So full suite should be **241 passed** after this step.

- [ ] **Step 7: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" add backend/app/services/era_poster.py && git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" commit -m "refactor(backend): era_poster owns helpers + new process_era_file"
```

---

## Task 2: Backend — refactor `era_posting.commit_eras` to use `process_era_file`

The commit endpoint currently inlines the parse → EraFile-create → match-loop logic. Replace the inner body of the per-preview loop with a single call to `process_era_file`. Response shape unchanged (aggregates counts across previews).

**Files:**
- Modify: `backend/app/routers/era_posting.py`

- [ ] **Step 1: Replace `commit_eras` function body**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/era_posting.py`.

Replace imports near `commit_eras` (currently at line 154-158):
```python
from app.models.claim import EraFile as EraFileModel
from app.models.denial import Denial
from app.models.payment import Payment
from app.services.audit_service import log_action
from app.services.era_poster import post_claim
```

with:
```python
from app.services.era_poster import process_era_file
```

Replace the entire `commit_eras` function (currently `@router.post("/era-posting/{session_id}/commit")` and its body) with:

```python
@router.post("/era-posting/{session_id}/commit")
def commit_eras(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    entry = import_sessions.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="session not found or expired")

    previews: List[EraFilePreview] = entry.payload["previews"]
    user_email = current_user.get("email")

    totals = {
        "claims_posted": 0, "claims_already_posted": 0, "claims_unmatched": 0,
        "claims_reversal_flagged": 0, "claims_cb_skipped": 0, "claims_malformed": 0,
        "payments_created": 0, "denials_created": 0,
    }
    errors: list = []

    for p in previews:
        file_path = p.__dict__.get("_file_path", "")
        try:
            with open(file_path, "r") as f:
                content = f.read()
        except OSError as exc:
            errors.append({"filename": p.source_filename,
                           "message": f"could not re-read file: {exc}"})
            continue
        result = process_era_file(db, content, p.source_filename, user_email)
        totals["claims_posted"] += result.claims_posted
        totals["claims_already_posted"] += result.claims_already_posted
        totals["claims_unmatched"] += result.claims_unmatched
        totals["claims_reversal_flagged"] += result.claims_reversal_flagged
        totals["claims_cb_skipped"] += result.claims_cb_skipped
        totals["claims_malformed"] += result.claims_malformed
        totals["payments_created"] += result.payments_created
        totals["denials_created"] += result.denials_created
        errors.extend(result.errors)

    import_sessions.purge(session_id)

    return {
        "files_processed": len(previews),
        "claims_posted": totals["claims_posted"],
        "claims_already_posted": totals["claims_already_posted"],
        "claims_unmatched": totals["claims_unmatched"],
        "claims_reversal_flagged": totals["claims_reversal_flagged"],
        "claims_cb_skipped": totals["claims_cb_skipped"],
        "claims_malformed": totals["claims_malformed"],
        "payments_created": totals["payments_created"],
        "denials_created": totals["denials_created"],
        "errors": errors,
    }
```

- [ ] **Step 2: Run the era_posting test suite**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_era_posting_commit.py tests/test_era_posting_upload.py -v 2>&1 | tail -15
```
Expected: all 9 tests PASS (5 commit + 4 upload, from Phase 2c). The response shape is identical, so existing assertions hold.

- [ ] **Step 3: Run full suite**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ 2>&1 | tail -3
```
Expected: **241 passed**.

- [ ] **Step 4: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" add backend/app/routers/era_posting.py && git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" commit -m "refactor(backend): era_posting.commit_eras delegates to process_era_file"
```

---

## Task 3: Backend — rewire `waystar.sync_eras_sftp` + 1 new test

The SFTP sync endpoint has been broken since Phase 2c (`import_era_file` raises `NotImplementedError`). Rewire it to use `process_era_file`.

**Files:**
- Modify: `backend/app/routers/waystar.py`
- Create: `backend/tests/test_waystar_sync_uses_era_poster.py`

- [ ] **Step 1: Write failing test**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_waystar_sync_uses_era_poster.py`:

```python
"""Verify Waystar SFTP sync routes downloaded ERAs through process_era_file."""
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from app.models.claim import Claim, ClaimStatus, InsuranceOrder, EraFile as EraFileModel
from app.models.patient import Patient
from app.models.payment import Payment

FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def test_sync_eras_sftp_posts_matched_claims(client, db, tmp_path, monkeypatch):
    # Pre-link a patient + claim so one ERA CLP01 matches.
    p = Patient(patient_id="45740", first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    db.add(Claim(
        claim_number="V1", patient_id=p.id,
        patient_control_number="216059P45740",
        billed_amount=Decimal("253.76"),
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("253.76"),
    ))
    db.commit()

    # Copy the ERA fixture into a temp path so the sync can "read" it.
    sftp_copy = tmp_path / "sftp_downloaded.835"
    sftp_copy.write_bytes(FIXTURE.read_bytes())

    # Configure settings so the endpoint doesn't 400 on missing SFTP host.
    monkeypatch.setattr("app.config.settings.waystar_sftp_host", "dummy")

    # Patch the client factory to return a stub that "downloads" our local file.
    class _StubClient:
        def download_eras_sftp(self, remote_dir: str):
            return [str(sftp_copy)]

    with patch("app.routers.waystar.get_waystar_client", return_value=_StubClient()):
        r = client.post("/api/waystar/sync-eras-sftp")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["downloaded"] == 1
    assert len(body["results"]) == 1
    first = body["results"][0]
    assert first["status"] == "imported"
    assert first["claims_posted"] >= 1

    assert db.query(EraFileModel).count() == 1
    assert db.query(Payment).count() >= 1
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_waystar_sync_uses_era_poster.py -v 2>&1 | tail -10
```
Expected: FAIL. Current `sync_eras_sftp` calls `import_era_file` which raises `NotImplementedError`, resulting in a 500 error (or whatever the handler does with it).

- [ ] **Step 3: Rewire the endpoint**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/waystar.py`.

Near the top of the file, add:
```python
import os
```
if not already imported. (The current file doesn't import `os`.)

Find the `sync_eras_sftp` function body. Replace lines 147-164 (the block from `# Now parse each downloaded file...` through `return {"downloaded": ..., "results": results}`) with:

```python
        # Parse + post each downloaded file through the Phase 2c ERA pipeline.
        from app.services.era_poster import process_era_file

        results = []
        for fpath in downloaded:
            try:
                with open(fpath, "r") as f:
                    content = f.read()
                result = process_era_file(
                    db, content,
                    filename=os.path.basename(fpath),
                    user_email="waystar-sftp-sync",
                )
                results.append({
                    "file": os.path.basename(fpath),
                    "claims_posted": result.claims_posted,
                    "claims_unmatched": result.claims_unmatched,
                    "status": "imported" if result.claims_posted else "no_matches",
                })
            except Exception as e:
                results.append({
                    "file": os.path.basename(fpath),
                    "status": "error",
                    "error": str(e),
                })

        log_action(db, "WAYSTAR_SFTP_SYNC", "waystar",
                   description=f"SFTP sync: {len(downloaded)} files downloaded")
        return {"downloaded": len(downloaded), "results": results}
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_waystar_sync_uses_era_poster.py tests/ 2>&1 | tail -5
```
Expected: new test PASS + full suite **242 total** (241 prior + 1 new). Actually note: the `test_legacy_era_disabled.py` tests still exist at this point (they go in T4), so 241 prior + 1 new = **242 total**.

- [ ] **Step 5: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" add backend/app/routers/waystar.py backend/tests/test_waystar_sync_uses_era_poster.py && git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" commit -m "fix(backend): waystar SFTP sync now posts via process_era_file + test"
```

---

## Task 4: Backend — delete `upload_file` endpoint + delete `test_legacy_era_disabled.py`

**Files:**
- Modify: `backend/app/routers/imports.py` (delete `upload_file` + its imports; keep `list_era_files`)
- Delete: `backend/tests/test_legacy_era_disabled.py`

- [ ] **Step 1: Delete `upload_file` function**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/imports.py`.

Remove these imports from the top of the file:
```python
from app.parsers.file_importer import import_file
from app.services.era_import_service import import_era_file
```

Remove the `@router.post("/upload")` decorator and the entire `upload_file(...)` function (roughly lines 18-77). Also remove:
```python
import os
import uuid
from datetime import datetime
from fastapi import APIRouter, File, UploadFile, Depends, HTTPException, Request
from app.database import get_db
from app.config import settings
from app.services.audit_service import log_action
```
only if they're no longer used by the remaining `list_era_files` function. Read the remaining body to confirm which imports stay — `list_era_files` typically only needs `APIRouter`, `Depends`, `Session`, `get_db`, and `EraFile` (imported inside the function).

**After editing, the file should contain ONLY:**

```python
"""Import routing — file history endpoint (upload moved to per-format routers in Phase 2c)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("/era-files")
def list_era_files(db: Session = Depends(get_db)):
    from app.models.claim import EraFile
    files = db.query(EraFile).order_by(desc(EraFile.imported_at)).limit(100).all()
    return [
        {
            "id": str(f.id),
            "filename": f.filename,
            "payer_name": f.payer_name,
            "check_number": f.check_number,
            "check_date": str(f.check_date) if f.check_date else None,
            "check_amount": float(f.check_amount or 0),
            "transaction_count": f.transaction_count,
            "status": f.status,
            "imported_at": f.imported_at.isoformat() if f.imported_at else None,
        }
        for f in files
    ]
```

(If the current file's `list_era_files` body differs slightly, preserve what's there; only delete `upload_file` and its dependencies.)

- [ ] **Step 2: Delete `test_legacy_era_disabled.py`**

```bash
rm /Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_legacy_era_disabled.py
```

This removes 3 tests:
- `test_legacy_import_era_file_raises_not_implemented` (asserted ERA import raises, irrelevant after T5 deletes the function)
- `test_legacy_helpers_still_importable` (the helpers are now in `era_poster`, not `era_import_service`)
- `test_legacy_imports_upload_era_returns_410` (endpoint is now gone — 404 not 410)

- [ ] **Step 3: Run full suite**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ 2>&1 | tail -3
```
Expected: **239 passed** (242 prior − 3 deleted legacy tests). If the suite shows fewer than 239, investigate; `upload_file` deletion shouldn't break anything except the `test_legacy_imports_upload_era_returns_410` test (which we deleted).

- [ ] **Step 4: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" add backend/app/routers/imports.py backend/tests/test_legacy_era_disabled.py && git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" commit -m "chore(backend): delete legacy /imports/upload endpoint + legacy test file"
```

(The `git add` of the deleted test file stages the deletion.)

---

## Task 5: Backend — delete `era_import_service.py` + `file_importer.py`

With T1–T4 done, these two files have no remaining callers. Delete them.

- [ ] **Step 1: Verify no remaining callers**

```bash
grep -rn "era_import_service\|file_importer" /Users/wwcclaudecode/Documents/wwc-era-project/backend/app/ --include="*.py" 2>&1 | head -10
```
Expected: **no output** (no remaining references after T1–T4).

- [ ] **Step 2: Delete the two files**

```bash
rm /Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/era_import_service.py
rm /Users/wwcclaudecode/Documents/wwc-era-project/backend/app/parsers/file_importer.py
```

- [ ] **Step 3: Run full suite**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ 2>&1 | tail -3
```
Expected: **239 passed**. If any test fails with ImportError, there's a lingering reference we missed in T1-T4.

- [ ] **Step 4: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" add backend/app/services/era_import_service.py backend/app/parsers/file_importer.py && git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" commit -m "chore(backend): delete era_import_service.py and file_importer.py"
```

---

## Task 6: Backend — orphan Payment cleanup script + 2 tests

**Files:**
- Create: `backend/app/scripts/prune_orphan_payments.py`
- Create: `backend/tests/test_prune_orphan_payments.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_prune_orphan_payments.py`:

```python
"""Tests for the one-time orphan Payment cleanup."""
from datetime import date
from decimal import Decimal
import pytest
from app.models.claim import Claim, ClaimStatus
from app.models.patient import Patient
from app.models.payment import Payment, PaymentType


def _seed(db):
    p = Patient(patient_id="P1", first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    c = Claim(claim_number="C1", patient_id=p.id, status=ClaimStatus.PENDING,
              billed_amount=Decimal("100"), balance=Decimal("0"))
    db.add(c); db.commit(); db.refresh(c)
    # Valid payment tied to existing claim
    db.add(Payment(claim_id=c.id, payment_type=PaymentType.INSURANCE_PAYMENT,
                   amount=Decimal("50"), payment_date=date.today()))
    # Orphan payment with fabricated claim_id
    db.add(Payment(claim_id="00000000-0000-0000-0000-000000000099",
                   payment_type=PaymentType.INSURANCE_PAYMENT,
                   amount=Decimal("25"), payment_date=date.today()))
    db.commit()
    return c


def test_prune_deletes_only_orphans(db):
    from app.scripts.prune_orphan_payments import run
    c = _seed(db)
    assert db.query(Payment).count() == 2
    deleted = run(confirm=True, session=db)
    assert deleted == 1
    remaining = db.query(Payment).all()
    assert len(remaining) == 1
    assert remaining[0].claim_id == c.id


def test_prune_refuses_without_confirm_flag(db):
    from app.scripts.prune_orphan_payments import run
    _seed(db)
    with pytest.raises(SystemExit):
        run(confirm=False, session=db)
    assert db.query(Payment).count() == 2
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_prune_orphan_payments.py -v 2>&1 | tail -10
```
Expected: both FAIL with `ModuleNotFoundError: No module named 'app.scripts.prune_orphan_payments'`.

- [ ] **Step 3: Create the script**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/scripts/prune_orphan_payments.py`:

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

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_prune_orphan_payments.py tests/ 2>&1 | tail -5
```
Expected: 2 new PASS + full suite **241 total** (239 prior + 2 new = 241).

- [ ] **Step 5: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" add backend/app/scripts/prune_orphan_payments.py backend/tests/test_prune_orphan_payments.py && git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" commit -m "feat(backend): prune_orphan_payments one-time migration script + tests"
```

---

## Task 7: Frontend — strip legacy drop zone from ImportFiles.jsx

**Files:**
- Modify: `frontend/src/pages/ImportFiles.jsx`

- [ ] **Step 1: Remove state hooks + handlers**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/ImportFiles.jsx`.

Delete these state hooks inside `ImportFiles`:
```js
const [dragging, setDragging] = useState(false)
const [uploading, setUploading] = useState(false)
const [result, setResult] = useState(null)
const [error, setError] = useState(null)
const inputRef = useRef()
```

(Keep the other state hooks: `bootstrapState`, `bootstrapInputRef`, `eraState`, `eraInputRef`, `chargeState`, `chargeInputRef`, `workflowFilter`.)

Delete the functions `handleFile`, `onDrop`, and `formatIcon` in their entirety.

- [ ] **Step 2: Update page header**

Find the `<h1 className="text-2xl font-bold text-gray-900 mb-2">Import Files</h1>` and the following `<p>` subtitle. Replace with:

```jsx
<h1 className="text-2xl font-bold text-gray-900 mb-2">Import</h1>
<p className="text-gray-500 text-sm mb-6">
  Three upload flows below: <strong>Charge Analysis</strong> creates claims from PrimeSuite,
  <strong>Claims Analysis</strong> links Claim IDs + workflow fields, <strong>ERA 835</strong>
  posts payments. Uploaded file history is at the bottom.
</p>
```

- [ ] **Step 3: Delete legacy drop zone JSX**

Find and delete the `{/* Drop Zone */}` block (typically `<div className="border-2 border-dashed rounded-xl p-10 ...">` with its inner `<input>` and conditional `{uploading ? (...) : (...)}` render). This block uses `inputRef`, `handleFile`, `onDrop`, `dragging`, `uploading` — all being removed.

Also delete:
- The `{result && (...)}` block (typically `<div className="card border border-green-200 bg-green-50 ...">` rendering file summary + `data_preview` / `text_preview`).
- The `{error && (...)}` block (`<div className="card border border-red-200 bg-red-50 ...">` at the legacy position).

KEEP:
- The Phase 2c amber banner (see next step — we're deleting it too but separately noted).
- The Bootstrap / ERA / Charge Analysis cards.
- The ERA file history section at the bottom.

- [ ] **Step 4: Delete Phase 2c amber banner**

Find the block starting with `{/* Phase 2c banner */}` and containing the amber warning about legacy ERA auto-posting being disabled. Delete the entire banner — the legacy drop zone it warned about is now gone.

- [ ] **Step 5: Clean up `lucide-react` import**

Find the import line:
```js
import { Upload, FileText, CheckCircle, AlertCircle, Clock, Database, Link2 } from 'lucide-react'
```

Remove `Upload` (was used only by the deleted drop zone). Keep the others. If any of `FileText`, `CheckCircle`, `AlertCircle`, `Clock`, `Database`, `Link2` are no longer used anywhere in the file after the deletions, remove those too.

Check usage by searching:
```bash
grep -c "AlertCircle\|CheckCircle\|FileText\|Clock\|Database\|Link2" /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/ImportFiles.jsx
```
Keep only icons with count > 0.

- [ ] **Step 6: Smoke-verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -8
```
Expected: success.

- [ ] **Step 7: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" add frontend/src/pages/ImportFiles.jsx && git -C /Users/wwcclaudecode/Documents/wwc-era-project -c user.email="wwcclaudecode@WWCs-Mac-mini.local" -c user.name="WWC Claude Code" commit -m "feat(frontend): strip legacy drop zone + amber banner from ImportFiles"
```

---

## Task 8: Manual verification + run prune script

**Files:** none — runtime verification.

- [ ] **Step 1: Full backend test suite**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ 2>&1 | tail -3
```
Expected: **241 passed** (baseline 241 − 3 deleted + 3 new).

- [ ] **Step 2: Grep for remaining legacy references**

```bash
grep -rn "era_import_service\|file_importer" /Users/wwcclaudecode/Documents/wwc-era-project/backend/ --include="*.py" 2>&1 | grep -v "venv/" | head -10
grep -rn "/imports/upload" /Users/wwcclaudecode/Documents/wwc-era-project/backend/ /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/ --include="*.py" --include="*.jsx" --include="*.js" 2>&1 | grep -v "venv/" | head -10
```
Expected: **both empty** — no references remain anywhere.

- [ ] **Step 3: Run the prune script against dev DB**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m app.scripts.prune_orphan_payments --yes-i-am-sure
```
Expected: `Pruned N orphan Payment rows.` where N is the count (around 1955 based on earlier inspection; may differ if you've run a wipe since). Re-run:
```bash
python -m app.scripts.prune_orphan_payments --yes-i-am-sure
```
Expected: `Pruned 0 orphan Payment rows.` (idempotent).

- [ ] **Step 4: Start dev stack and verify UI**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000 &
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run dev &
sleep 5 && curl -sS http://localhost:8000/api/health
```

- [ ] **Step 5: Manual UI checklist**

At http://localhost:3000/imports:

- [ ] Page header reads "Import" (not "Import Files") + 3-flow subtitle.
- [ ] NO generic drop zone at the top.
- [ ] NO amber "Legacy ERA auto-posting is disabled" banner.
- [ ] Claims Analysis Import card visible.
- [ ] ERA 835 Payment Posting card visible.
- [ ] Charge Analysis Import card visible.
- [ ] ERA File Import History card at the bottom still shows previously imported ERAs.

- [ ] **Step 6: Verify `/imports/upload` is gone**

```bash
curl -sS -o /dev/null -w "upload endpoint: %{http_code}\n" -X POST http://localhost:8000/api/imports/upload 2>&1
```
Expected: **404** (endpoint deleted — not 410, not 401).

- [ ] **Step 7: Kill dev servers**

```bash
kill %1 %2 2>/dev/null
```

No commit for this task.

---

## Summary

**Total new tests:** 3 backend tests (1 Waystar sync + 2 prune script).
**Total deleted tests:** 3 backend tests (from `test_legacy_era_disabled.py`).
**Net test delta:** 0. Final count: **241**.

**Total commits:** 7 feature commits (T1–T7) + 1 verification task (no commit).

**Files deleted:**
- `backend/app/services/era_import_service.py`
- `backend/app/parsers/file_importer.py`
- `backend/tests/test_legacy_era_disabled.py`

**Files created:**
- `backend/app/scripts/prune_orphan_payments.py`
- `backend/tests/test_prune_orphan_payments.py`
- `backend/tests/test_waystar_sync_uses_era_poster.py`

**Files modified:**
- `backend/app/services/era_poster.py` — add moved helpers + `ProcessResult` + `process_era_file`.
- `backend/app/routers/era_posting.py` — `commit_eras` delegates to `process_era_file`.
- `backend/app/routers/waystar.py` — `sync_eras_sftp` rewired; now works again.
- `backend/app/routers/imports.py` — `upload_file` deleted, only `list_era_files` remains.
- `frontend/src/pages/ImportFiles.jsx` — legacy drop zone + handlers + amber banner deleted, header updated.

**After this plan:** single canonical ERA ingest pipeline (`era_poster.process_era_file`) used by both UI commit endpoint and Waystar SFTP sync. No pre-2b code survives. 1,955 orphan Payment rows pruned (one-time).
