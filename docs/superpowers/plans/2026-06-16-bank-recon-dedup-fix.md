# Bank Recon — fix auto-exclusion: date+amount+last4 identity, drop date-range coverage, ignore pending

> subagent-driven-development. Backend TDD; suite baseline 69 failed / 0 errors. Frontend build + headless load before deploy. FINANCIAL-INTEGRITY code — tests are mandatory; the whole point is to stop silently losing real bank transactions.

**Branch:** `feat/bank-recon-dedup-fix` off `main`.

## The bug (confirmed)
The BAI import "overlap guard" auto-excludes any candidate whose **date** falls within a prior import's date range (`bank_recon.py` `covered_by_prior_import`, lines 125-133 / 160-165 / 294-307). That silently drops genuinely-new transactions that merely share a date with a prior import — e.g. a transaction that was **pending** (skipped) in an earlier file and later clears, or any late-posting deposit. There's no override.

## Required behavior (from user)
1. **Auto-exclusion identity = (date, amount, last_4)** matched against transactions ALREADY in a prior BAI file. (Replaces the date-range coverage block AND the formatted_text dedup_key cross-import match.)
2. **Ignore all PENDING transactions entirely** — not imported, **not even logged/counted**. Only FINAL (posted) transactions are considered.
3. A final transaction that is **not already part of a BAI file** (by the date+amount+last4 fields check) **and not explicitly (manually) excluded** → **included in the current BAI file, regardless of date.**

## Current state (verified)
- Parser `backend/app/services/bai2_generator.py`: `_filter_rows` reads date/desc/amount columns; `_parse_date` already strips a "PENDING - mm/dd/yyyy" prefix (so pending currently gets IMPORTED — wrong). `_parse_description` yields `last_4`. `dedup_key = sha256(date|amount|formatted_text)` (line 240). Skip counters: withdrawal/modmed/stripe/zero/duplicate_in_file/always_drop. NO pending counter.
- Recon `backend/app/routers/bank_recon.py`: `/preview` builds snapshot incl `covered_by_prior_import` (date-range) + per-txn `already_imported`(dedup_key)/`date_already_covered`(date-range). `/generate` blocks `excluded ∪ dedup_key-in-DB ∪ covered_keys`.
- Frontend `frontend/src/pages/BankRecon.jsx`: auto-checks rows where `already_imported || date_already_covered` (lines 53, 78); stats "Already imported" + "Date already imported" (180-181); badges (233-237).

---

## B1 — Drop pending silently at parse
**File:** `bai2_generator.py`, test.
In `_filter_rows`, at the very top of the per-row loop (after reading `date_raw`/`desc`/`amt_raw`), drop pending rows with `continue` and **no counter**:
```python
def _is_pending(row, date_raw) -> bool:
    if "PENDING" in str(date_raw or "").upper():
        return True
    for k, v in row.items():
        if k and "status" in k.lower() and "PENDING" in str(v or "").upper():
            return True
    return False
...
    if _is_pending(r, date_raw):
        continue   # not final → ignore entirely (not imported, not counted)
```
(Do NOT add a `skipped_pending` field.) Keep `_parse_date`'s prefix-strip (harmless now that pending is dropped first, and tolerant of odd formats). Test `tests/test_bank_recon_pending_dropped.py`: a CSV with one PENDING row + one posted row → `parse_csv_from_bytes` returns ONLY the posted txn; ALL `skipped_*` counters are 0 (pending not counted); a status-column "Pending" variant is also dropped. Commit `feat(bank-recon): drop pending transactions at parse, uncounted (B1)`.

---

## B2 — Identity-based auto-exclusion (date+amount+last4); remove date-range coverage
**File:** `bank_recon.py`, test.
Define a prior-identity helper used by both preview + generate:
```python
def _prior_identities(db) -> set:
    rows = db.query(Bai2Transaction.transaction_date, Bai2Transaction.amount,
                    Bai2Transaction.last_4).all()
    return {(d, _q2(a), (l4 or "")) for (d, a, l4) in rows}   # _q2 = round to 2dp / Decimal-normalize
def _identity(t):   # t: ParsedTransaction
    return (t.transaction_date, _q2(t.amount), (t.last_4 or ""))
```
- **/preview:** compute `prior = _prior_identities(db)`. Per candidate set `already_imported = _identity(t) in prior`. **Remove** `date_already_covered` (always False / drop the field) and **remove** `covered_by_prior_import` from the snapshot. Stats: `already_imported_count = sum(already_imported)`; drop/zero `date_covered_count`. Store in snapshot `already_imported_keys = [t.dedup_key for t if already]` (so generate enforces the same identity decision the user previewed).
- **/generate:** `prior = _prior_identities(db)`; `excluded = set(payload.excluded_keys)`. `new_txns = [t for t in parsed.transactions if t.dedup_key not in excluded and _identity(t) not in prior]`. **No date-range block.** `skipped_prior = count of identity-in-prior (not manually excluded)`. Everything else imported regardless of date.
- Keep within-file dedup + `dedup_key` as-is (storage identity / unique constraint unchanged).
Tests `tests/test_bank_recon_identity_dedup.py` (db fixture; insert a prior Bai2Import+Bai2Transaction):
  - prior txn (05/01, $500, last4 1234); candidate same date+amt+last4 but DIFFERENT description → `already_imported True`, generate skips it (re-worded duplicate caught).
  - candidate (05/03, $250, last4 9999) whose date is INSIDE the prior import's date range but no identity match → `already_imported False`, **imported** (the bug fix — regardless of date).
  - candidate identical identity but user put its key in `excluded_keys` → not imported (manual still wins).
  - candidate with different amount or different last4 on a covered date → imported.
Suite ≤ baseline. Commit `feat(bank-recon): auto-exclude only true date+amount+last4 duplicates; import new txns regardless of date (B2)`.

---

## F1 — Frontend: reflect identity-based exclusion
**File:** `frontend/src/pages/BankRecon.jsx`.
- Auto-check (exclude) only `t.already_imported` (remove the `|| t.date_already_covered` in both the initial effect ~line 53 and `resetExclusions` ~78).
- Remove the "Date already imported" stat (line 181) and the `date_already_covered` badge branch (234-237). Keep the "already imported" badge. Row amber-highlight keyed on `t.already_imported` only.
- Optional: tweak the "already imported" badge tooltip to "Already in a prior BAI file (same date, amount, last-4) — re-worded duplicates are caught too."
Build clean. Commit `feat(bank-recon): UI reflects identity-based exclusion (drop date-coverage badge) (F1)`.

---

## F2 — Headless smoke + deploy
1. build + vite preview + Playwright load `/billing/bank-recon` (or `/billing`) → /login, 0 console errors.
2. Merge to main; deploy backend then frontend; smoke (health 200, `/billing` 200); push origin.
3. Authed check: re-import a file where a previously-pending transaction has cleared → it imports (not dropped); re-upload a file overlapping a prior import → only true date+amount+last4 dupes are auto-excluded, new same-date txns come in.

## Out of scope / notes
- Pending detection keys off the "PENDING" marker (date cell or a status column). If the bank uses a different word for not-final, flag it — easy to extend.
- Identity (date+amount+last4): two genuinely-distinct deposits with identical date+amount+last4 (e.g. both null last4) would be treated as one — accept per the user's identity definition; note it.
- Within-file dedup + dedup_key formula unchanged (storage/unique constraint).
