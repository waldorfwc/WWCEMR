# Phase 2d — Claims Analysis Workflow Enrichment

**Date:** 2026-04-21
**Project:** wwc-era-project
**Depends on:** Phase 2a (edit drawer, `PATCH /claims/{id}` allow-list), Phase 2b (imported claims), Phase 2c (Claims Analysis matcher + bootstrap endpoint)
**Blocks:** Phase 2e (dedicated follow-up queue page + legacy cleanup)

## Goal

Extend the existing `/api/imports/claim-id-bootstrap` endpoint to patch four more workflow fields on matched claims from the Claims Analysis `.xls`: `status` (mapped from the `"Claim Status"` column), `follow_up_date`, `follow_up_reason`, `last_submission_date`, and `claim_state`. Surface those fields in the Phase 2a edit drawer under a new "Workflow" section and on the `/claims` list page with a Follow-up column + filter chips.

## Workflow

1. Billing/admin user uploads the same Claims Analysis `.xls` they already use for Phase 2c bootstrap.
2. Preview card now additionally shows: `"Will set status on N · follow-up on M · state on K"`.
3. Commit patches all primary matches (PCN + status + follow-up + state + last-submission) AND creates secondary/tertiary claim records (also inheriting workflow fields from the Claims Analysis row).
4. Opening any claim in the edit drawer shows a new "Workflow" section with 4 inputs editable via the existing PATCH endpoint.
5. `/claims` list has a new "Follow-up" column (color-coded for overdue / due-soon / other) and filter chips `Open only`, `Needs follow-up`, `Overdue`.

## Non-goals

- Dedicated `/follow-up` page (deferred to later phase).
- `Filing Method`, `Filing Plan Name`, `Electronic Filing Method ID` columns (99% constant in real data, low value).
- Auto-derivation of `claim_state` from `status`.
- Follow-up notifications / alerts / emails.
- Follow-up history (audit log captures changes; no dedicated history view).
- Follow-up reason vocabulary enforcement (freeform VARCHAR).
- Claim state picklist enforcement (VARCHAR, not enum).
- Multi-file Claims Analysis batch upload.
- Claims Analysis reconciliation / diff report.
- Cross-row validation between status and state.
- Admin-UI migration button (terminal command only).
- Auto-cascading status ↔ state updates on edit.

## Status mapping

From Claims Analysis `Claim Status` column → our `ClaimStatus` enum:

| Claims Analysis value | → | `ClaimStatus` |
|---|---|---|
| `"Paid In Full"` | → | `PAID` |
| `"Paid Partial"` | → | `PARTIAL` |
| `"New/No EOB"` | → | `PENDING` |
| anything else | → | **unchanged**, warning issued |

Case-insensitive, whitespace-tolerant.

## Override policy

Claims Analysis always wins on re-import. Any field in the Claims Analysis row overwrites our stored value on every commit, regardless of what was there (including an ERA-set status). If Claims Analysis row has `status = null` (unknown value), we leave the existing status unchanged. Decision made because Claims Analysis is the human-curated snapshot — generated on demand, always the latest truth at import time.

## Backend

### One-time migration — `backend/app/scripts/add_phase2d_columns.py`

```python
"""One-time migration — add Phase 2d workflow columns to claims table.

Idempotent: re-runs check via PRAGMA table_info and skip existing columns.

Usage (from backend/):
    source venv/bin/activate
    python -m app.scripts.add_phase2d_columns
"""
from typing import Dict, List
from sqlalchemy import text
from app.database import SessionLocal

NEW_COLUMNS = [
    ("follow_up_date", "DATE"),
    ("follow_up_reason", "VARCHAR(200)"),
    ("last_submission_date", "DATE"),
    ("claim_state", "VARCHAR(20)"),
]


def run() -> Dict[str, List[str]]:
    db = SessionLocal()
    added: List[str] = []
    skipped: List[str] = []
    try:
        existing = {row[1] for row in db.execute(text("PRAGMA table_info(claims)")).fetchall()}
        for name, type_ in NEW_COLUMNS:
            if name in existing:
                skipped.append(name)
                continue
            db.execute(text(f"ALTER TABLE claims ADD COLUMN {name} {type_}"))
            added.append(name)
        db.commit()
    finally:
        db.close()
    return {"added": added, "skipped": skipped}


def main() -> None:
    result = run()
    for col in result["added"]:
        print(f"  + {col}")
    for col in result["skipped"]:
        print(f"  = {col} (already exists)")


if __name__ == "__main__":
    main()
```

SQLite-specific (`PRAGMA table_info`, `ALTER TABLE ADD COLUMN`). Matches the project's single-database deployment assumption. No backfill — columns start NULL for all existing rows.

### Model change — `backend/app/models/claim.py`

Add four nullable columns below the existing `notes` field:
```python
    notes = Column(Text, nullable=True)

    # Phase 2d enrichment (from Claims Analysis)
    follow_up_date = Column(Date, nullable=True)
    follow_up_reason = Column(String(200), nullable=True)
    last_submission_date = Column(Date, nullable=True)
    claim_state = Column(String(20), nullable=True)   # "Open" | "Closed"
```

Fresh SQLite installs get the columns automatically via `Base.metadata.create_all()`. Existing DBs need the one-time script.

### Extend `claims_analysis_matcher.py`

#### Required columns
Extend `REQUIRED_COLUMNS`:
```python
REQUIRED_COLUMNS = [
    "Patient ID", "Claim ID", "Date of Service", "Claim Amount",
    "Insurance Priority", "Claim Status", "Claim State",
]
```

Optional columns (read opportunistically, don't fail if missing):
- `"Follow-Up Date"`
- `"Follow-Up Reason"`
- `"Last Submission Date"`

#### `ClaimsAnalysisGroup` — extend with 5 fields
```python
@dataclass
class ClaimsAnalysisGroup:
    patient_external_id: str
    claim_id: str
    dos: Optional[date]
    total_amount: Decimal
    row_count: int
    insurance_priority: str
    internal_claim_id: str
    # Phase 2d
    claim_status_raw: Optional[str]            # raw "Claim Status" string
    claim_state: Optional[str]                 # "Open" | "Closed"
    follow_up_date: Optional[date]
    follow_up_reason: Optional[str]
    last_submission_date: Optional[date]
```

Parse logic: first-row-wins per `(Patient ID, Claim ID)` group. Skip `NaN` gracefully (values become `None`).

#### Status mapping helper
Add to `claims_analysis_matcher.py`:
```python
from app.models.claim import ClaimStatus

CLAIMS_STATUS_MAP = {
    "paid in full": ClaimStatus.PAID,
    "paid partial": ClaimStatus.PARTIAL,
    "new/no eob": ClaimStatus.PENDING,
}


def map_claim_status(raw: Optional[str]) -> Optional[ClaimStatus]:
    """Return ClaimStatus enum for a Claims Analysis status string, or None if unknown."""
    if not raw:
        return None
    return CLAIMS_STATUS_MAP.get(raw.strip().lower())
```

Unknown values → matcher emits `ParseIssue` warning, returns `None` (commit will leave existing status alone).

### Extend `claim_id_bootstrap.py` commit

Modify `_patch_claim` to accept the `ClaimsAnalysisGroup` instead of just the internal_claim_id string:
```python
def _patch_claim(db: Session, claim_id: str, group: ClaimsAnalysisGroup,
                 user_email: str) -> Claim:
    from app.services.claims_analysis_matcher import map_claim_status

    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    mapped_status = map_claim_status(group.claim_status_raw)

    old = {
        "patient_control_number": claim.patient_control_number,
        "status": claim.status.value if claim.status else None,
        "follow_up_date": str(claim.follow_up_date) if claim.follow_up_date else None,
        "follow_up_reason": claim.follow_up_reason,
        "last_submission_date": str(claim.last_submission_date) if claim.last_submission_date else None,
        "claim_state": claim.claim_state,
    }

    claim.patient_control_number = group.internal_claim_id
    if mapped_status is not None:
        claim.status = mapped_status
    claim.follow_up_date = group.follow_up_date
    claim.follow_up_reason = group.follow_up_reason
    claim.last_submission_date = group.last_submission_date
    claim.claim_state = group.claim_state

    db.commit()

    new = {
        "patient_control_number": group.internal_claim_id,
        "status": claim.status.value if claim.status else None,
        "follow_up_date": str(claim.follow_up_date) if claim.follow_up_date else None,
        "follow_up_reason": claim.follow_up_reason,
        "last_submission_date": str(claim.last_submission_date) if claim.last_submission_date else None,
        "claim_state": claim.claim_state,
    }
    log_action(
        db, "UPDATE", "claim",
        resource_id=str(claim.id),
        patient_id=str(claim.patient_id) if claim.patient_id else None,
        user_name=user_email, old_values=old, new_values=new,
        description="claim-id-bootstrap: patched workflow fields",
    )
    return claim
```

Modify `_create_secondary` to ALSO set all 4 workflow fields + status on the new secondary Claim row (copied from the source group).

Modify `commit_claim_id_bootstrap` — update the `_patch_claim` call to pass the whole `group` object.

### Extend `PATCH /api/claims/{id}` allow-list

In `backend/app/routers/claims.py`, extend `EDITABLE_CLAIM_FIELDS`:
```python
EDITABLE_CLAIM_FIELDS = {
    ...existing...,
    "follow_up_date",
    "follow_up_reason",
    "last_submission_date",
    "claim_state",
}
```

And extend `DATE_FIELDS`:
```python
DATE_FIELDS = {
    "date_of_service_from", "date_of_service_to", "check_date",
    "statement_date", "received_date",
    "follow_up_date", "last_submission_date",   # NEW
}
```

### Extend `GET /api/claims` list filtering

Add two optional query params to `list_claims()`:
```python
def list_claims(
    ...,
    state: Optional[str] = None,             # "open" | "closed"
    has_followup: Optional[bool] = None,     # True → claim_state=Open AND follow_up_date <= today
):
    ...
    if state == "open":
        q = q.filter(Claim.claim_state == "Open")
    elif state == "closed":
        q = q.filter(Claim.claim_state == "Closed")
    if has_followup:
        q = q.filter(
            Claim.follow_up_date != None,
            Claim.follow_up_date <= date.today(),
            Claim.claim_state == "Open",
        )
```

### Extend `_claim_to_dict` response

Include the 4 new fields in both detailed and list responses:
```python
"claim_state": claim.claim_state,
"follow_up_date": str(claim.follow_up_date) if claim.follow_up_date else None,
"follow_up_reason": claim.follow_up_reason,
"last_submission_date": str(claim.last_submission_date) if claim.last_submission_date else None,
```

## Frontend

### `ImportFiles.jsx` — rename card

Change Bootstrap card title to `"Claims Analysis Import"`. Update subtitle to reflect the broader scope. Update `BootstrapSuccess` copy to mention status/follow-up updates.

### `EditClaimDrawer.jsx` — new "Workflow" section

Add below the existing "Status & Notes" section:
```jsx
<Section title="Workflow">
  <Field label="Claim state">
    <select
      className="input w-full py-1 text-[12px]"
      value={fields.claim_state || ''}
      onChange={(e) => set('claim_state', e.target.value || null)}
    >
      <option value="">—</option>
      <option value="Open">Open</option>
      <option value="Closed">Closed</option>
    </select>
  </Field>
  <Field label="Follow-up date">
    <Date value={fields.follow_up_date} onChange={v => set('follow_up_date', v)} />
  </Field>
  <Field label="Follow-up reason">
    <Text value={fields.follow_up_reason} onChange={v => set('follow_up_reason', v)} />
  </Field>
  <Field label="Last submission date">
    <Date value={fields.last_submission_date} onChange={v => set('last_submission_date', v)} />
  </Field>
</Section>
```

Add the 4 keys to `EDITABLE_CLAIM_FIELDS` constant in that file. `useClaimEdit.js` already does diff-based saving — no changes there.

### `Claims.jsx` — Follow-up column + filter chips

Add a new column between existing ones:
```jsx
<th>Follow-up</th>
```

Cell render:
```jsx
{claim.follow_up_date && (
  <div className={followUpColor(claim.follow_up_date, claim.claim_state)}>
    <div>{fmt.date(claim.follow_up_date)}</div>
    {claim.follow_up_reason && (
      <div className="text-[10px] text-gray-400">{claim.follow_up_reason}</div>
    )}
  </div>
)}
```

`followUpColor(date, state)` utility:
- `state === "Closed"` → `"text-gray-400"` (regardless of date).
- `date < today` → `"text-red-600 font-semibold"` (overdue).
- `date <= today + 7` → `"text-amber-600"` (due soon).
- else → `"text-gray-600"`.

Filter chip bar (above table or in existing filter section):
```jsx
<div className="flex gap-2 mb-3">
  <FilterChip active={filter === 'all'} onClick={() => setFilter('all')}>All</FilterChip>
  <FilterChip active={filter === 'open'} onClick={() => setFilter('open')}>Open only</FilterChip>
  <FilterChip active={filter === 'followup'} onClick={() => setFilter('followup')}>Needs follow-up</FilterChip>
  <FilterChip active={filter === 'overdue'} onClick={() => setFilter('overdue')}>Overdue</FilterChip>
</div>
```

React Query param wiring:
- `all` → no extra params.
- `open` → `?state=open`.
- `followup` → `?has_followup=true` (which implies `state=open AND date<=today`).
- `overdue` → same as followup (same SQL); UI label reflects urgency. (Both chips use the same filter for now; if we later want "due today only" as a separate filter, we add a param.)

### Access control

Already enforced at router level. Claims Analysis Import stays BILLING-only. No new permissions.

## Files touched

**Backend — created:**
- `backend/app/scripts/add_phase2d_columns.py`
- `backend/tests/test_add_phase2d_columns.py`
- `backend/tests/test_claims_list_filters.py` (or extend `test_claim_edit.py`)

**Backend — modified:**
- `backend/app/models/claim.py` — 4 new columns.
- `backend/app/services/claims_analysis_matcher.py` — extend dataclass + REQUIRED_COLUMNS + parse logic + map_claim_status helper.
- `backend/app/routers/claim_id_bootstrap.py` — modify `_patch_claim`, `_create_secondary`, `commit_claim_id_bootstrap`.
- `backend/app/routers/claims.py` — extend `EDITABLE_CLAIM_FIELDS`, `DATE_FIELDS`, `list_claims()`, `_claim_to_dict()`.
- `backend/tests/test_claims_analysis_parser.py` — extend with Phase 2d tests.
- `backend/tests/test_claim_id_bootstrap_commit.py` — extend with Phase 2d tests.
- `backend/tests/test_claim_edit.py` — extend PATCH tests.

**Frontend — modified:**
- `frontend/src/pages/ImportFiles.jsx` — rename card title + subtitle.
- `frontend/src/components/EditClaimDrawer.jsx` — new Workflow section + 4 keys added to EDITABLE_CLAIM_FIELDS.
- `frontend/src/pages/Claims.jsx` — new Follow-up column + filter chips.

**One-time execution:**
- `cd backend && source venv/bin/activate && python -m app.scripts.add_phase2d_columns`

## Verification

### Automated (pytest)

Run `pytest backend/tests/` — all existing 218 tests + 22 new = **240 total**.

### Manual UI checklist

- [ ] Migration runs cleanly: `added: [follow_up_date, follow_up_reason, last_submission_date, claim_state]`. Second run: `skipped: [same 4]`.
- [ ] Bootstrap card title now reads "Claims Analysis Import" with updated subtitle.
- [ ] Upload `Claim Analysis 2026.01.xls`, commit → success card lists status/follow-up counts.
- [ ] Open a claim on `/claims/:id` → Edit drawer has new "Workflow" section with 4 populated fields for any matched claim.
- [ ] Save a manual edit to `follow_up_date` in the drawer → persists.
- [ ] `/claims` list page shows new "Follow-up" column with color-coded dates.
- [ ] Filter chip "Overdue" filters to claims with `follow_up_date < today AND claim_state == "Open"`.
- [ ] Filter chip "Open only" filters to `claim_state == "Open"`.
- [ ] Filter chip "Needs follow-up" filters to `has_followup=true`.

## Tests (backend)

### `test_add_phase2d_columns.py` (2 tests)
1. `test_migration_adds_all_four_columns`.
2. `test_migration_is_idempotent`.

### `test_claims_analysis_parser.py` extension (8 tests)
3. `test_parse_reads_claim_status_and_state`.
4. `test_parse_reads_follow_up_date`.
5. `test_parse_reads_follow_up_reason_preserves_string` — `"2-Claim Sent <15D"` preserved.
6. `test_parse_reads_last_submission_date`.
7. `test_parse_real_fixture_status_distribution` — counts: 429 PAID / 34 PARTIAL / 799 PENDING.
8. `test_status_mapping_known_values` — case-insensitive, whitespace-tolerant.
9. `test_status_mapping_unknown_returns_none`.
10. `test_parse_warns_on_unknown_status`.

### `test_claim_id_bootstrap_commit.py` extension (5 tests)
11. `test_commit_sets_claim_status_from_mapping`.
12. `test_commit_overrides_existing_status_from_era`.
13. `test_commit_sets_all_four_workflow_fields`.
14. `test_commit_secondary_claim_inherits_workflow_fields`.
15. `test_commit_audit_includes_new_fields_in_new_values`.

### `test_claim_edit.py` extension — PATCH allow-list (4 tests)
16. `test_patch_updates_follow_up_date`.
17. `test_patch_updates_follow_up_reason`.
18. `test_patch_updates_claim_state`.
19. `test_patch_updates_last_submission_date`.

### `test_claims_list_filters.py` (new, 3 tests)
20. `test_list_claims_filter_state_open_closed`.
21. `test_list_claims_filter_has_followup_true`.
22. `test_claim_response_includes_new_fields`.

**Total: 22 new tests.** Full suite: `218 + 22 = 240`.

## Open questions

None blocking.
