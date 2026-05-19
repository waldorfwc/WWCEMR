# Code Helper — design spec

**Date**: 2026-05-19
**Scope**: New feature under Billing. AI-assisted CPT + ICD-10 code generation from a clinical note, with payer-aware denial-list awareness and a persistent request history.

## Problem + goal

Billing staff currently read clinical notes and pick CPT + ICD-10 codes by hand. Errors and denied claims are common, and staff have institutional knowledge ("Cigna always denies 97110") that lives only in their heads.

**Goal**: a one-page tool where staff paste or upload a note, get back well-justified CPT codes (with modifiers) and 4 ICD-10 positions at the highest specificity the note supports, with explicit warnings + alternatives for codes that match the practice's known denial list.

## Out of scope

- Anything that actually *submits* a claim to a clearinghouse — this is decision support only.
- Per-CPT pricing / reimbursement estimation.
- Bulk import of many notes at once.
- HCPCS Level II codes (J-codes etc.) — CPT + ICD-10 only for v1.
- LCD/NCD coverage lookup — denial list is the practice's own observed denials, not Medicare LCDs.

## Architecture

Single AI round-trip per request. Backend route accepts the note + optional payer, builds a Claude prompt that includes the active denial-list entries matching the payer, calls `claude-opus-4-7` with forced tool-use, validates the structured response, and persists everything.

```
[browser] /billing/code-helper
   │  paste note OR upload PDF + pick payer
   ▼
[Cloud Run frontend  →  Cloud Run backend]
   POST /api/billing/code-helper/requests
                  │
                  ├──► fetch active denial-list entries
                  │    (payer match OR untagged)
                  │
                  ├──► Anthropic messages.create
                  │    model=claude-opus-4-7
                  │    tool_choice=submit_coding
                  │
                  ├──► Pydantic-validate the tool input
                  │
                  ├──► roster-match patient
                  │    (last_name + dob → patients table)
                  │
                  └──► persist CodeHelperRequest row
                       audit_log entry
   ◄──── full structured response
```

Reuses the existing AI-call pattern from `surgery_billing_ai.py` and `billing_doc_classify.py`.

## Data model

Two new tables.

### `code_helper_requests`

One row per AI call. Retained verbatim so the audit log is reproducible.

| Column | Type | Notes |
|---|---|---|
| id | GUID PK |  |
| requested_at | DateTime | default utcnow |
| requested_by | String(120) | user email |
| **Input** | | |
| note_text | Text, nullable | pasted text branch |
| source_pdf_storage_filename | String(255), nullable | uploaded-pdf branch; reuses billing_doc_storage convention |
| payer_name | String(120), nullable | "Cigna" / "Aetna" / etc. |
| **Patient** | | |
| patient_name | String(160), nullable | AI-extracted, user-editable |
| patient_dob | Date, nullable | AI-extracted, user-editable |
| patient_id | String(20) FK→patients.patient_id, nullable | set when roster match is unambiguous |
| **AI output (verbatim)** | | |
| cpt_codes | JSON, default `[]` | list of CPT entries — shape below |
| icd10_codes | JSON, default `[]` | up to 4 entries — shape below |
| **Audit** | | |
| ai_model | String(60) | e.g. `claude-opus-4-7` |
| ai_input_tokens | Integer, nullable | from response usage |
| ai_output_tokens | Integer, nullable | from response usage |
| error | Text, nullable | non-null if the AI call failed |

Indexes:
- `(requested_at desc)` — for the history list
- `(patient_id)` — "all coding requests for this patient"
- `(requested_by)`

### `code_helper_denials`

| Column | Type | Notes |
|---|---|---|
| id | GUID PK |  |
| code | String(20) | the CPT or ICD code that gets denied |
| code_type | String(10) | `cpt` or `icd10` |
| payer_name | String(120), nullable | null = applies to all payers |
| reason | Text, nullable | free text — why this gets denied |
| is_active | Boolean, default true | soft-disable without delete |
| added_by | String(120) | user email |
| added_at | DateTime | default utcnow |
| updated_at | DateTime | bumped on edits |

Index on `(code, payer_name, is_active)` for fast lookup at request time.

### CPT entry JSON shape

```json
{
  "code": "99214",
  "modifiers": ["25"],
  "position": 1,
  "justification_type": "e_m_mdm",
  "justification": {
    "problems_addressed": "Moderate (2+ stable chronic illnesses)",
    "data_reviewed":      "Limited (1 external lab reviewed)",
    "risk":               "Moderate (Rx management for chronic illness)"
  },
  "time_minutes": null,
  "denial_flag":  null,
  "alternative":  null
}
```

- `justification_type` is one of `e_m_mdm`, `e_m_time`, `procedure`.
- For `e_m_mdm`: `justification` is the structured object above; `time_minutes` is null.
- For `e_m_time`: `justification` is a free-text sentence summarizing what was done; `time_minutes` is the integer minute count documented.
- For `procedure`: `justification` is a free-text "medical necessity met because…" sentence; `time_minutes` is null.
- `modifiers`: list of 2-char strings (`"25"`, `"59"`, `"RT"`, etc.).
- `denial_flag` (when set): `{"payer": "Cigna", "reason": "97110 not separately reimbursable"}`.
- `alternative` (when set): `{"code": "99213", "modifiers": [], "rationale": "Drops level by one; note documents only a problem-focused exam."}`.

### ICD-10 entry JSON shape

```json
{ "code": "E11.9", "position": 1, "description": "Type 2 diabetes mellitus without complications" }
```

Up to 4 entries. Each at the highest specificity the note supports.

## API endpoints

All under `/api/billing/code-helper`. Permissions:
- GETs: `claim:read`
- POST / PATCH on requests + denials: `claim:edit`
- DELETE on requests + denials: `user:manage` (admin only)

| Method | Path | Purpose |
|---|---|---|
| POST | `/requests` | multipart: `note_text` (str) OR `note_pdf` (file) + `payer_name?`. Triggers AI, persists, returns the saved row + structured output. |
| GET | `/requests` | paginated, newest-first, supports `?patient_id=` + `?payer=` filters |
| GET | `/requests/{id}` | full row with all justifications |
| PATCH | `/requests/{id}` | edit `patient_name` / `patient_dob` only (post-AI corrections) |
| DELETE | `/requests/{id}` | admin-only |
| GET | `/denials` | list denial entries; supports `?payer=` + `?active=true` filters |
| POST | `/denials` | `{code, code_type, payer_name?, reason?}` |
| PATCH | `/denials/{id}` | edit any field, including `is_active` toggle |
| DELETE | `/denials/{id}` | admin-only |

## AI prompt

Single `messages.create` call, tool-use forced via `tool_choice={"type":"tool","name":"submit_coding"}`.

**System prompt** (short, OB/GYN context):
> You are an expert medical coder for a women's health practice. Given a clinical note plus a list of CPT/ICD-10 codes that get denied by specific payers, return the most accurate codes the note supports. Use ICD-10 at the highest level of specificity the note documents — do not invent specificity that isn't present. For each CPT, choose the correct justification type (E&M MDM, E&M time-based, or procedure) and provide the structured rationale. If a suggested code is on the supplied denial list for the current payer, flag it and propose the next-best alternative that is still supported by the note.

**User content**:
1. The note (text or `{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":...}}` for PDF).
2. The active denial-list entries that match the request's payer OR have `payer_name IS NULL`, rendered as a simple table.
3. Patient extraction instruction: "Also extract patient name and DOB if present in the note. Leave null if not present."

**Tool input schema** mirrors the data model exactly. `justification` is typed as `oneOf [object, string]` so E&M can carry the structured object and procedures can carry a string. Validated server-side.

**Model**: `claude-opus-4-7`. Same model used by `surgery_billing_ai` and `billing_doc_classify` — production quality at a known cost. Tokens-out enforced to ~1500 max.

## Patient roster matching

After the AI returns name + DOB:

1. Normalize: trim, lowercase last name.
2. Query `patients` where `LOWER(last_name) = ?` AND `dob = ?`.
3. **Exact-one match** → set `request.patient_id = match.patient_id`, UI shows "matched to chart #…"
4. **Zero matches** → `patient_id` stays null, UI shows "no chart match"
5. **Multiple matches** → `patient_id` stays null, UI shows "2 possible matches" with a picker that user can resolve and PATCH onto the row.

## Frontend

Page at `/billing/code-helper`, linked from TopNav under Billing (next to Bank Recon). Single page with three vertical zones.

### Input panel (top)

- Tab toggle: **Paste note** | **Upload PDF**
- Paste view: large textarea
- Upload view: file dropzone; shows name + size after select
- Payer dropdown: pre-populated from distinct `payer_name` values seen on existing `claims`, plus free-text "Other"
- **Generate codes** button — disabled until note is provided. Shows spinner + "Calling Claude…" during the 4–8 s AI call.

### Result panel (renders after AI returns, before save)

- **Patient strip** — Name + DOB inputs pre-filled from AI, editable. Small badge:
  - "✓ Matched to chart #12345 (Smith, John)" — when single roster match
  - "No chart match" — when zero
  - "2 possible matches" + picker — when ambiguous
- **CPT section** — each suggested CPT as a card:
  - Header: code + modifier chips (`99214` `-25`), position pill, justification-type tag
  - **▶ View justification** expander:
    - E&M MDM → 3-row structured display (problems / data / risk)
    - E&M time → "X min — <summary>"
    - Procedure → free-text rationale
  - If `denial_flag` set: amber/red banner + **Use alternative (99213)** button that swaps in the alternative
- **ICD-10 section** — Pos 1 → Pos 4, code + description chips
- **Save** persists; **Discard** drops without saving

### History table (below the fold)

- Columns: Patient · DOB · Date/Time · Payer · CPT chips · ICD-10 chips · Status (saved/error)
- Sortable headers + per-column filters (same UX pattern as the LARC list)
- **Row click** → opens a right-side detail drawer (read-only): original note, patient strip, all CPTs with full justifications + denial flags + chosen alternatives, all ICD-10s, AI model + token usage

### Denial-list admin (sub-page)

`/billing/code-helper/denials`. Reached via small "Manage denial list" link on the main page.

- Table: code · type · payer · reason · active toggle · added by · added at
- **+ Add denial** button → small modal (code, type, optional payer, optional reason)
- Inline active/inactive toggle; admin-only delete

## Error handling

| Failure | Response | Persisted? |
|---|---|---|
| Anthropic timeout / network | 502, "AI call failed, retry" | Yes, with `error` field |
| AI response fails schema validation | 502, raw response logged | Yes, with `error` |
| `ANTHROPIC_API_KEY` unset | 503 "AI not configured" | No |
| PDF corrupt / unreadable | 422 | No |
| PDF >10 MB or >20 pages | 422 | No |
| AI returns 0 CPT codes | 200, UI surfaces "no codes extracted" | Yes |
| Roster match ambiguous | 200, `patient_id=null`, UI shows candidates | Yes |
| Invalid modifier (not in standard list) | 200, AI suggestion still saved, UI shows ⚠ chip on that modifier | Yes |

Every successful request also writes an `audit_logs` row (existing `audit_service`) with action `code_helper_generated`.

## Testing

**Unit** (`backend/tests/test_code_helper_*.py`):
- Pydantic validation of AI tool output — good + 6 known-bad payloads (missing required, wrong types, justification shape mismatch for E&M vs procedure).
- Denial-list filter by payer (matching, untagged-universal, inactive excluded).
- Patient roster matching: exact-one, ambiguous, no-match.
- Modifier validation against a small built-in list of valid 2-char modifiers.

**Integration** (FastAPI `TestClient` + `respx`-mocked Anthropic API):
- POST `/requests` with text input — happy path
- POST `/requests` with PDF input — happy path
- Denial-flag flow: payer-tagged denial entry → AI prompt includes it → mocked AI returns a flagged CPT → response carries the flag through
- GET `/requests` pagination + filters
- PATCH editing patient name/DOB
- 502 path on mocked AI failure

**Manual smoke**: 5 real anonymized notes provided by Oliver, eyeball output before declaring v1 ready.

## Cost + ops

- Per request: ~$0.05–$0.10 (1–2K input tokens, ~500–1500 output, claude-opus-4-7 pricing).
- At an estimated 20 requests/day average, daily cost ~$1–$2; monthly ~$30–$60.
- Token counts stored per request → simple SQL daily/monthly report.
- No rate limit in v1; revisit if cost surprises.

## Migration notes

Schema additions go via `_apply_lightweight_migrations` in `database.py` (the existing pattern). New tables get auto-created by `Base.metadata.create_all()` on backend boot — no separate migration script needed for v1.

## Permissions

Settling on existing permissions (no new perm key for v1):
- `claim:read` — read history, view detail, list denials
- `claim:edit` — create requests, edit patient strip, create/edit denial entries
- `user:manage` — delete denial entries, delete requests (admin only)

## Open questions / future work

- HCPCS Level II (J-codes for injectables, A-codes for DME) — explicit scope for v2 if useful.
- Bulk operations (paste 10 notes, get 10 sets of codes) — not requested for v1.
- Real LCD/NCD lookup — would need a third-party data source.
- Auto-link to `claims` table when a code-helper result is used for a real claim — defer to v2 once the workflow is observed.
