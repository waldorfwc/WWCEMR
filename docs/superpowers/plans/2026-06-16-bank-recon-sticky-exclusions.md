# Bank Recon — sticky per-transaction exclusions + admin review list

> subagent-driven-development. Backend TDD; suite baseline 69 failed / 0 errors. Frontend build + headless load before deploy. Financial-integrity code.

**Branch:** `feat/bank-recon-sticky-exclusions` off `main`.

**Goal:** Manual exclusions become **sticky** — a transaction the user excludes at generate is remembered by identity (date + amount + last_4) and auto-excluded on future uploads (flagged "previously excluded"). An **admin review list** shows all sticky exclusions and lets a manager **reinstate** (un-stick) one so it can import again.

## Current state (verified)
- `bank_recon.py`: `/preview` (WORK) flags `already_imported` (identity vs prior imports). `/generate` (WORK) skips `excluded_keys` (user unchecks) + identity-dupes; records only a COUNT in notes — exclusions are NOT persisted. List `/imports` (VIEW); delete/restore `/imports/{id}` (MANAGE).
- Identity helpers `_q2`, `_identity`, `_prior_identities` already exist in `bank_recon.py`.
- `Bai2Import` uses `SoftDeleteMixin`. `Bai2Transaction` fields: transaction_date, amount, last_4, description, formatted_text, dedup_key.
- Frontend `BankRecon.jsx`: single page; preview rows auto-check `already_imported`; history via `/bank-recon/imports`.

## Decisions
- Sticky identity = `(date, amount, last_4)` (same as dedup identity).
- Sticky exclusions are **enforced** at generate (auto-skipped) — to re-include, **reinstate** via the admin list (not a per-import override).
- Only persist sticky exclusions for transactions the user excluded that were **otherwise importable** (NOT already-imported dupes) — excluding a dupe is just "don't double-import," not "never import."

---

## B1 — `Bai2Exclusion` model + helper
**Files:** create `backend/app/models/bai2_exclusion.py`; register import in `database.py`; test.
```python
class Bai2Exclusion(Base, SoftDeleteMixin):   # deleted_at = "reinstated"
    __tablename__ = "bai2_exclusions"
    id = Column(GUID(), primary_key=True, default=new_uuid)
    exclusion_key = Column(String(64), nullable=False, unique=True)  # sha256(date|amount|last4)
    transaction_date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    last_4 = Column(String(4), nullable=True)
    description = Column(Text, nullable=True)        # formatted_text/desc for display
    reason = Column(Text, nullable=True)
    excluded_by = Column(String(200), nullable=True)
    source_import_id = Column(GUID(), nullable=True) # the import where it was first excluded
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    # SoftDeleteMixin: deleted_at (reinstated_at), deleted_by (reinstated_by)
```
Add to `bank_recon.py`: `_exclusion_key(date, amount, last_4)` = sha256(`f"{date}|{_q2(amount)}|{last_4 or ''}"`); `_active_exclusion_identities(db)` → set of `(date, _q2(amount), last_4 or "")` where `deleted_at IS NULL`. Migration: table auto-creates via Base metadata (registered import) — confirm like other models. Test: model import + helper round-trip. Commit `feat(bank-recon): Bai2Exclusion model + identity helpers (B1)`.

---

## B2 — Persist + enforce sticky exclusions in preview/generate
**File:** `bank_recon.py`, test.
- `GenerateRequest`: add optional `exclusion_reason: Optional[str] = None`.
- `/generate`: after computing `excluded` (user keys) + `prior` (imported identities):
  - `sticky = _active_exclusion_identities(db)`.
  - Skip set now also blocks sticky: `new_txns = [t for t in parsed.transactions if t.dedup_key not in excluded and _identity(t) not in prior and _identity(t) not in sticky]`.
  - **Persist new sticky exclusions:** for each `t` where `t.dedup_key in excluded and _identity(t) not in prior` (user excluded an otherwise-importable txn), UPSERT a `Bai2Exclusion` by `exclusion_key` — if a row exists (even soft-deleted/reinstated), reactivate it (`deleted_at=None`) and refresh fields; else insert. Set description=t.formatted_text, reason=payload.exclusion_reason, excluded_by=current_user email, source_import_id. Count `skipped_sticky` separately from `skipped_user_excluded`.
  - Do NOT persist exclusions for already_imported dupes.
- `/preview`: compute `sticky = _active_exclusion_identities(db)`; per candidate add `previously_excluded = _identity(t) in sticky` (distinct from `already_imported`). Stats: add `previously_excluded_count`. (These should be auto-checked + blocked, same as already_imported.)
Tests `tests/test_bank_recon_sticky.py` (real /preview+/generate, reuse the storage + pg-lock fixtures from test_bank_recon_identity_dedup.py):
  - Upload file, exclude a non-dup txn at generate → a Bai2Exclusion row is created; it's NOT imported.
  - Re-upload a file containing that same (date+amount+last4) txn → preview flags `previously_excluded=True`; generate does NOT import it even with `excluded_keys=[]`.
  - Excluding an already-imported dup does NOT create a sticky exclusion.
Suite ≤ baseline. Commit `feat(bank-recon): persist + enforce sticky per-transaction exclusions (B2)`.

---

## B3 — Admin list + reinstate endpoints
**File:** `bank_recon.py`, test.
- `GET /bank-recon/exclusions` (Tier.VIEW), optional `?include_reinstated=false`: return active sticky exclusions (deleted_at IS NULL) newest-first: `{id, transaction_date, amount, last_4, description, reason, excluded_by, created_at}`. (If include_reinstated, also show reinstated with reinstated_at/by.)
- `POST /bank-recon/exclusions/{id}/reinstate` (Tier.MANAGE): soft-delete the exclusion (`soft_delete(email)`), so the transaction can import again. 404 if missing; idempotent if already reinstated.
- (Optional) `POST /bank-recon/exclusions` (Tier.MANAGE) to manually add one by date+amount+last4 — include if low-cost; else skip.
Tests: list returns active; reinstate flips it (then a re-upload of that txn imports). Commit `feat(bank-recon): list + reinstate endpoints for sticky exclusions (B3)`.

---

## F1 — Preview "previously excluded" badge
**File:** `frontend/src/pages/BankRecon.jsx`.
Auto-check rows where `already_imported || previously_excluded` (extend the existing auto-exclude filters). Add a distinct badge "previously excluded" (e.g. slate/indigo) with tooltip "You excluded this before — it stays out until reinstated in the Excluded list." Add a stat "Previously excluded" (`previously_excluded_count`). Row highlight on either flag. Build clean. Commit `feat(bank-recon): preview flags previously-excluded transactions (F1)`.

---

## F2 — Admin review list (Excluded transactions)
**File:** `frontend/src/pages/BankRecon.jsx` (a collapsible "Excluded transactions" panel/section below the import history; MANAGE-gated controls).
- `useQuery(['bank-recon-exclusions'], () => api.get('/bank-recon/exclusions'))`. Render a table: date, amount, last4, description, reason, excluded by, when, + a **Reinstate** button per row (MANAGE only; `POST /bank-recon/exclusions/{id}/reinstate` → invalidate the query + history). Confirm before reinstate ("Reinstate this transaction? It will be importable again on the next upload."). Empty → "No sticky exclusions." Gate the panel/reinstate on `tier(MODULE.BANK_RECON, TIER.MANAGE)` via useCurrentUser (VIEW can see list; MANAGE can reinstate). Build clean. Commit `feat(bank-recon): admin Excluded-transactions review list with reinstate (F2)`.

---

## F3 — Headless smoke + deploy
1. build + vite preview + Playwright load `/billing/bank-recon` → /login, 0 console errors.
2. Merge to main; deploy backend then frontend; smoke (health 200, `/billing` 200, `/api/bank-recon/exclusions` 401 noauth); push origin.
3. Authed check: exclude a txn → it appears in the Excluded list; re-upload its file → it's auto-excluded ("previously excluded"); reinstate it → re-upload imports it.

## Out of scope
- Pattern/rule-based exclusions (option 2) — not this round; sticky is per-transaction identity only.
- No change to dedup/pending behavior from the prior fix.
