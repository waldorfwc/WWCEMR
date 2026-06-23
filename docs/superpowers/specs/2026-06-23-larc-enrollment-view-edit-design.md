# LARC Enrollment Form — View & Edit From the Card

**Date:** 2026-06-23
**Module:** LARC / device tracking
**Status:** Approved (brainstorm complete)

## Goal

From the enrollment card on a LARC assignment, reception can:

1. **Preview** what the enrollment form will contain *before* sending (resolved field
   values + a blank-field warning list) — so nothing silently sends blank.
2. After sending, **view/download** the live BoldSign document PDF.
3. After sending, **edit** the form in BoldSign's embedded editor (in place, same
   document), while the envelope is still editable.

## Background

Today the enrollment flow (see `enrollment_sender.send_enrollment_envelope`) builds
the form field values on the fly from the assignment row + practice config, pushes
them to BoldSign via `POST /v1/template/send`, and persists a `LarcEnrollmentEnvelope`
row holding only the `boldsign_envelope_id` + status/fax tracking. **The field values
are never stored locally**, and after send there is no way to view or edit the form
from our app — only void + resend.

This caused a real incident: reception sent an envelope that arrived blank because
Practice Profile + patient demographics were empty, and there was no way to see that
before (or after) sending.

## Verified BoldSign capabilities

Confirmed against BoldSign docs + the .NET `DocumentClient` reference:

- **`CreateEmbeddedEditUrl`** — `POST /v1/document/createEmbeddedEditUrl?documentId=<id>`
  returns an embedded URL that loads BoldSign's prepare/edit page for an existing
  (sent / in-progress) document. Supports `viewOption` (`PreparePage`), `redirectUrl`,
  `showToolbar`, `showSendButton`, `showSaveButton`, `showPreviewButton`.
- **Edit restrictions:** before any signer signs, all fields/recipients are editable.
  Once a signer signs, that signer's fields/files can't be changed and they can't be
  removed; fields for not-yet-signed signers remain editable; title/signing order/files
  can't change after send. BoldSign returns an error if an edit URL is requested for a
  document that can't be edited (e.g., completed/declined/revoked).
- **`DownloadDocument`** — `GET /v1/document/download?documentId=<id>` returns the
  current PDF at any status (in-progress shows partially-signed state). We already use
  this pattern in `boldsign_envelopes.download_signed_pdf`.
- Embedded signing is already enabled on the account and our domain is whitelisted
  (the surgery patient portal uses `GetEmbeddedSignLink`).

Our flow signs in order Reception → Patient → Provider, so immediately after send
(nobody has signed) the form is fully editable.

## Scope

### In scope
- A read-only **preview** of resolved enrollment field values + blank-field warnings,
  available before send (and after send as a quick "what's on file" view).
- **View/download** the live document PDF after send.
- **Edit** the document in BoldSign's embedded editor after send (in place), with a
  clear editability guard and graceful failure → fall back to existing void+resend.

### Out of scope (YAGNI)
- Replacing the current one-click server-side send with an embedded prepare-and-send
  flow (the before-send preview is a native summary, not a BoldSign render).
- Storing form field values locally / a `form_fields` column.
- Editing before send beyond what the existing ClinicianPicker + options already allow.
- Any change to the webhook, fax, or signing-order logic.

## Architecture

Editing happens **in place** on the same `boldsign_envelope_id`, so no new envelope
row is created on edit and the existing webhook → status → auto-fax pipeline is
untouched. The three new backend endpoints are thin: one reuses the existing field
builders to produce a preview; two wrap new BoldSign client calls.

### Components

**1. Field-preview resolver** — `enrollment_sender.resolve_enrollment_preview(db, assignment) -> dict`

Reuses the existing template-spec selection and `_build_*_fields` logic to produce a
**flat, human-readable** list of the values that would be sent, plus a list of blank
required fields. Returns:

```python
{
    "template": "Nexplanon",            # nice name of the resolved template
    "device_type": "Nexplanon",
    "fields": [                          # ordered, human-readable
        {"label": "Patient Name", "value": "Jane Doe", "blank": False},
        {"label": "Patient DOB", "value": "01/02/1990", "blank": False},
        {"label": "Primary Insurance", "value": "", "blank": True},
        # ...
    ],
    "blanks": ["Primary Insurance", "Inserting Provider NPI"],  # labels only
    "sendable": True,                    # False if a hard-required field is blank
}
```

Implementation note: the existing `_build_*_fields` functions return
`{role: [{id, value}]}` keyed by opaque BoldSign field IDs. The resolver adds a
small, per-template **field-ID → human label** map (a module-level dict, one per
template) so the preview is readable. Dates render MM/DD/YYYY. "Blank" = value is
empty/None after building. "sendable" is False only when a field the send path
already treats as required (patient email, inserting provider email, device type,
template) is missing — matching `send_enrollment_envelope`'s own preconditions.

**2. BoldSign client additions** — `enrollment_sender` (LARC-local, alongside the existing send code)

- `create_embedded_edit_url(env: LarcEnrollmentEnvelope, *, redirect_url: str) -> str`
  - Calls `POST /v1/document/createEmbeddedEditUrl?documentId=<env.boldsign_envelope_id>`
    with body `{"redirectUrl": redirect_url, "viewOption": "PreparePage",
    "showToolbar": true, "showSaveButton": true, "showSendButton": true,
    "showPreviewButton": true}`.
  - Returns the `editFormUrl`/`url` from the response.
  - Raises `EnrollmentNotEditable` (custom exception) if BoldSign returns a 4xx
    indicating the document can't be edited.
- `download_envelope_pdf(env: LarcEnrollmentEnvelope) -> tuple[bytes, str]`
  - Calls `GET /v1/document/download?documentId=<env.boldsign_envelope_id>`.
  - Returns `(pdf_bytes, filename)` where filename is
    `enrollment-<patient_last>-<short_envelope_id>.pdf`.

Both use the same authenticated httpx client/header pattern already used by
`send_enrollment_envelope` (API key from env/Secret Manager — no secrets in code).

**3. Router endpoints** — `backend/app/routers/larc.py`, all gated
`requires_tier(Module.LARC, Tier.WORK)`:

- `GET /assignments/{assignment_id}/enrollment/preview`
  - 404 if assignment missing; 400 if not a pharmacy_order flow.
  - Returns the `resolve_enrollment_preview` dict. Works whether or not an envelope
    has been sent (it previews the *current* resolved data).
- `GET /envelopes/{envelope_id}/edit-url?redirect=<path>`
  - 404 if envelope missing.
  - Editability guard: if `env.status` not in `{"sent", "partially_signed"}` →
    409 with `{"detail": "not_editable", "reason": "<status>"}`.
  - Builds an absolute `redirect_url` from the configured app base URL + the passed
    `redirect` path (defaults to the assignment page).
  - Calls `create_embedded_edit_url`; on `EnrollmentNotEditable` → 409
    `{"detail": "not_editable", "reason": "boldsign_rejected"}`.
  - Returns `{"url": "<embedded edit url>"}`.
  - Audit-logs `enrollment_edit_url_issued` with envelope id + actor.
- `GET /envelopes/{envelope_id}/document`
  - 404 if envelope missing.
  - Streams the PDF (`StreamingResponse`/`Response`, `media_type="application/pdf"`,
    `Content-Disposition: inline; filename="…"`).
  - On BoldSign error → 502 `{"detail": "document_unavailable"}`.

### Frontend (`frontend/src/pages/LarcAssignment.jsx`)

**Before send — in `EnrollmentSentBody`:**
- Add a **Preview Form** secondary button next to "Send Enrollment via BoldSign".
- Clicking opens a `EnrollmentPreviewModal` (new small component) that GETs
  `/assignments/{id}/enrollment/preview` and renders the field table. Blank fields are
  highlighted; a top banner lists the blanks: "3 fields are blank — they'll send empty:
  Primary Insurance, Inserting Provider NPI…". If `sendable` is false, a red note:
  "Patient email is required before sending."

**After send — in `EnrollmentEnvelopeStatus`:**
- Add two buttons under the signer badges:
  - **View Form** → opens `/envelopes/{eid}/document` in a new tab (inline PDF). Also
    available is **Preview** (the same native summary modal) for a quick text view.
  - **Edit Form** → only rendered when status ∈ {`sent`, `partially_signed`}. On click,
    GET `/envelopes/{eid}/edit-url?redirect=<current assignment path>`, then open the
    returned URL in a modal iframe (`EnrollmentEditModal`, new small component) sized for
    the BoldSign editor. On the BoldSign redirect back, the modal closes and the
    assignment refetches so updated status shows.
  - If edit-url returns 409, toast: "This form can no longer be edited because signing
    has progressed. Void and resend instead." (the existing void action remains the
    fallback).

Button labels use Title Case ("Preview Form", "View Form", "Edit Form"). All
user-facing dates render MM/DD/YYYY.

## Data flow

```
Before send:
  card → GET /assignments/{id}/enrollment/preview
       → resolve_enrollment_preview(db, assignment)  [reuses _build_*_fields]
       → {fields[], blanks[], sendable} → modal renders table + warnings

After send — view:
  card → open /envelopes/{eid}/document → download_envelope_pdf → inline PDF

After send — edit:
  card → GET /envelopes/{eid}/edit-url?redirect=/larc/...     (guard: status sent/partially_signed)
       → create_embedded_edit_url(env, redirect_url)
       → POST /v1/document/createEmbeddedEditUrl?documentId=...
       → {url} → iframe modal → reception edits in BoldSign → Save/Send
       → BoldSign redirects back → modal closes → assignment refetch
       → (signer signs later → existing webhook/fax pipeline, unchanged)
```

## Error handling

| Case | Behavior |
|---|---|
| Assignment not pharmacy_order | preview → 400 |
| Envelope id unknown | 404 |
| Edit requested, status not sent/partially_signed | 409 `not_editable` + reason |
| BoldSign rejects edit (completed/declined/revoked race) | 409 `not_editable` reason `boldsign_rejected` → toast → void+resend fallback |
| BoldSign download fails | 502 `document_unavailable` → toast |
| Required field blank (preview) | `sendable:false`, blanks listed; Send button still governed by existing server-side preconditions (no new blocking) |

## Testing

Backend (pytest, mock BoldSign HTTP):
- `resolve_enrollment_preview`: full data → no blanks, `sendable:true`; missing
  insurance + NPI → those labels in `blanks`; missing patient email → `sendable:false`;
  date renders MM/DD/YYYY; correct template label per device type.
- `GET /enrollment/preview`: 200 shape; 400 for non-pharmacy flow; Work-tier gated (403
  for view-only user).
- `GET /edit-url`: 200 `{url}` when status `sent` (mock BoldSign 200); 409 when status
  `signed`/`voided`/`faxed`; 409 when BoldSign returns 4xx; audit row written.
- `GET /document`: 200 `application/pdf` with inline disposition (mock download); 502 on
  BoldSign error.

Frontend: component smoke (preview modal renders blanks; Edit Form hidden when fully
signed) if the existing suite has comparable coverage; otherwise rely on backend tests
+ manual verification, matching repo conventions.

## Manual / docs

Update the LARC module manual (per the keep-in-sync rule) with a short "Viewing &
Editing the Enrollment Form" note: Preview before send (and the blank warning), View
Form after send, and Edit Form (and that it's unavailable once signing completes →
void & resend). Edit the seed section; insert-only seed propagates the new slug on
deploy.

## Files

- Modify: `backend/app/services/larc/enrollment_sender.py` (resolver, 2 client fns,
  `EnrollmentNotEditable`, label maps)
- Modify: `backend/app/routers/larc.py` (3 endpoints)
- Test: `backend/tests/.../test_larc_enrollment_view_edit.py` (new)
- Modify: `frontend/src/pages/LarcAssignment.jsx` (buttons + 2 small modal components,
  or split modals into `frontend/src/components/larc/EnrollmentPreviewModal.jsx` and
  `EnrollmentEditModal.jsx` if cleaner)
- Modify: `backend/app/services/manual_seed.py` (LARC manual section)
