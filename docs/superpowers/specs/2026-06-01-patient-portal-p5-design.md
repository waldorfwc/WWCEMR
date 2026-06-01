# Patient Portal — P5: Uploads + Self-Report

**Status:** Draft for review
**Author:** Claude Code, 2026-06-01
**Builds on:** P1 (auth + shell + dashboard), P2 (payments + scheduling), P3 (consent), P4 (documents)

## Goal

Close out the patient's pre-op task list by letting them:

1. **Self-report completed milestones** — "I had my labs done" and "I completed my hospital pre-op call" → flips the existing `labs_self_reported` and `hospital_preop_self_reported` flags from P1 T1.
2. **Download the clearance template** if their surgery requires clearance.
3. **Upload their completed clearance / EKG / pre-op paperwork.**
4. **See the status of each item** on the dashboard milestone list.

P5 ships the **upload infrastructure** (`SurgeryDocument` model + GCS bucket layout + multipart endpoint). P5b reuses that same infrastructure to layer in FMLA — separate from P5 because FMLA's $25 fee + completed-form return chain adds enough surface area to warrant its own spec.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Portal P5                                                          │
│                                                                     │
│  Dashboard CTAs:                                                    │
│    "I had my labs"           → POST /self-report/labs               │
│    "I completed pre-op call" → POST /self-report/hospital-preop     │
│    (idempotent; only flips when currently false)                    │
│                                                                     │
│  Documents page gains a Clearance card (when clearance_required):   │
│    Step 1: [Download template]                                      │
│             → GET /clearance/template                               │
│    Step 2: [Upload completed form / EKG]                            │
│             → POST /clearance/upload  (multipart)                   │
│    Status: 'Required' → 'Uploaded ✓' → 'Approved' (office sets last)│
│                                                                     │
│  GET /api/patient/portal/{sid}/uploads                              │
│    returns the patient's uploaded files (clearance, EKG, future     │
│    FMLA), with download links via signed-URL on demand.             │
│                                                                     │
│  GCS:                                                               │
│    gs://wwc-documents/clearance/template.pdf            (static)    │
│    gs://wwc-documents/surgery-uploads/{surgery_id}/...  (per-pt)    │
└─────────────────────────────────────────────────────────────────────┘
```

## New schema

### `SurgeryDocument` table

```python
class SurgeryDocument(Base):
    __tablename__ = "surgery_documents"
    id              = Column(GUID, primary_key=True, default=new_uuid)
    surgery_id      = Column(GUID, ForeignKey("surgeries.id",
                                                ondelete="CASCADE"),
                                nullable=False, index=True)
    kind            = Column(String(40), nullable=False)
        # "clearance", "ekg", "fmla_blank", "fmla_completed", or future kinds
    filename        = Column(String(255), nullable=False)
    gcs_path        = Column(String(500), nullable=False)
        # e.g. "surgery-uploads/<sid>/clearance/2026-06-01_clearance.pdf"
    content_type    = Column(String(100), nullable=True)
    size_bytes      = Column(Integer, nullable=True)
    uploaded_at     = Column(DateTime, default=datetime.utcnow,
                                nullable=False)
    uploaded_by     = Column(String(120), nullable=False)
        # "patient:portal" or staff email
```

### Existing Surgery columns to surface

These already exist; P5 adds endpoints that read + write them:

| Column | Purpose |
|---|---|
| `clearance_required` (Bool) | Whether clearance is needed for this surgery |
| `clearance_status` (String) | "not_required" / "required" / "uploaded" / "approved" |
| `labs_self_reported` (Bool) | From P1 T1 |
| `hospital_preop_self_reported` (Bool) | From P1 T1 |

P5 adds two `_at` columns on Surgery for the new self-report flips:

```python
# Already in surgery.py (P1 T1)
labs_self_reported              = Column(Boolean, default=False, nullable=False)
labs_self_reported_at           = Column(DateTime, nullable=True)
hospital_preop_self_reported    = Column(Boolean, default=False, nullable=False)
hospital_preop_self_reported_at = Column(DateTime, nullable=True)
```

No NEW columns needed beyond the new `SurgeryDocument` table.

## Self-report endpoints

```
POST /api/patient/portal/{sid}/self-report/labs
POST /api/patient/portal/{sid}/self-report/hospital-preop
```

Both are **idempotent**: flip the bool to `True` and stamp `_at` to `now()` **only if currently False** (no double-stamping if the patient clicks twice). Both return the current milestone state from the same payload shape as the dashboard endpoint, so the frontend can refresh in place.

Patient cannot UN-mark these themselves — if they clicked by mistake, coordinator clears it from the admin UI (not in scope for P5, but trivial to add).

## Clearance flow

### Template download

```
GET /api/patient/portal/{sid}/clearance/template
```

Streams `gs://wwc-documents/clearance/template.pdf` to the patient. Gated on `surgery.clearance_required == True` (otherwise 409 "Clearance isn't required for this surgery").

Practice uploads `clearance/template.pdf` once — patients downloading it get the same file regardless of procedure_classification, because the form is general (cardiologist clearance template applies to any hospital case).

### Patient upload

```
POST /api/patient/portal/{sid}/clearance/upload
  Content-Type: multipart/form-data
  fields:
    file: the PDF/image
    kind: "clearance" | "ekg"  (defaults to "clearance")
```

Returns the created `SurgeryDocument` row. Side-effect: sets `surgery.clearance_status = "uploaded"` and stamps an audit. Office reviews and bumps to `"approved"` from the admin UI (out of scope for P5).

Constraints:
- **Max file size:** 10 MB. Larger → 413 with patient-friendly message.
- **MIME types:** `application/pdf`, `image/jpeg`, `image/png`, `image/heic` (phone photos). Anything else → 415.
- **Storage path:** `gs://wwc-documents/surgery-uploads/{sid}/{kind}/{timestamp}_{safe-filename}.{ext}`.

### List uploads

```
GET /api/patient/portal/{sid}/uploads
```

Returns the patient's `SurgeryDocument` rows (no staff-uploaded docs from other tables), with each row including a fresh signed URL valid for 5 minutes. Frontend reads this to render the "Your uploaded files" list with download links.

## Frontend additions

### Dashboard — two new CTAs

The existing milestone list (P1) shows `labs_self_reported` and `hospital_preop_self_reported` rows. P5 adds a "Mark as done" button to each row that's not yet done. Clicking calls the self-report endpoint and the dashboard refreshes.

```jsx
// EnvelopeRow-style component, but for self-report milestones
{m.status === 'todo' && (m.key === 'labs' || m.key === 'hospital_preop') && (
  <button onClick={() => markAsDone(m.key)}>Mark as done</button>
)}
```

`markAsDone('labs')` → `POST /self-report/labs`. `markAsDone('hospital_preop')` → `POST /self-report/hospital-preop`.

### Documents page — new Clearance card

Appears on Documents page below Receipts, only when `surgery.clearance_required == True`. Two-step interaction:

```
┌─ Clearance ─────────────────────────────┐
│ Step 1: Download blank template          │
│   [Download clearance form]              │
│                                          │
│ Step 2: Upload your completed form / EKG │
│   [Choose file] [Upload]                  │
│                                          │
│ Status: Required → Uploaded → Approved   │
│                                          │
│ Your uploads:                            │
│   • clearance_form.pdf (uploaded 06-01)  │
│     [Download]                           │
└──────────────────────────────────────────┘
```

The status badge color tracks the same color scheme as other milestones (gray=required, amber=uploaded-not-yet-approved, green=approved).

## Service module

```python
# backend/app/services/surgery_uploads.py
ALLOWED_MIME = ("application/pdf", "image/jpeg", "image/png", "image/heic")
MAX_BYTES = 10 * 1024 * 1024


def store_upload(db, surgery, *, kind, filename, file_bytes,
                  content_type, uploaded_by) -> SurgeryDocument:
    """Validate, write to GCS, create the SurgeryDocument row. Raises
    UploadError for size or MIME violations."""

def signed_download_url(doc: SurgeryDocument, ttl_minutes=5) -> str:
    """V4 signed URL for the patient to download their own file."""

def stream_static_pdf(gcs_path: str) -> bytes:
    """Pull a static doc (e.g. clearance/template.pdf) from GCS.
    Reused by the clearance template endpoint and any future static
    download. None when the object doesn't exist."""
```

## What's NOT in P5 (defer)

- **FMLA flow** — P5b. Needs the $25 fee + completed-form return chain.
- **Coordinator review UI** for clearance uploads → not in scope; coordinator can update `clearance_status` via the existing admin UI today.
- **OCR / form-recognition** on uploaded EKGs to extract heart rate or rhythm → not happening, ever (clinical workflow).
- **Multi-file upload in one request.** P5 = one file per request. If the patient has multiple pages, they upload them one at a time.
- **Image preview thumbnail** for uploaded EKGs → out of scope; download is sufficient.

## Open questions

1. **Signed-URL TTL** — 5 minutes is a defensible default for patient-initiated downloads. Could tighten to 60 seconds if abuse surfaces.

2. **Filename collision** — two uploads with the same patient-provided filename get distinct `gcs_path` (timestamp prefix), but the displayed filename in the list will look duplicate. Acceptable; the upload date column disambiguates.

3. **What if the patient uploads the wrong file?** Currently no delete affordance. Coordinator can delete from admin UI (when that ships); patient can re-upload the right file and the office will use the most recent. Document this in the UI copy.

## Risks

- **GCS storage costs.** Trivial at this scale — patient uploads cap at 10 MB and the bucket is in a single region. Per-surgery overhead ~50 MB max.
- **Signed URL leakage.** A 5-min URL leaked from a patient's screen-share could be replayed during that window. Acceptable risk; the file is the patient's own PHI returning to them.
- **MIME spoofing.** A `.pdf.exe` could carry an arbitrary payload. Mitigation: we check MIME by Content-Type (which the client sets) AND by magic-byte sniff on the first 8 bytes. PDFs start with `%PDF`; JPEGs start with `FFD8FF`. If the magic bytes don't match the Content-Type, reject with 415.

## Tech stack

Same as P1–P4. New `google-cloud-storage` calls reuse the existing client. New GCS bucket folder structure under the already-existing `gs://wwc-documents` bucket — no new IAM beyond what's already granted (`objectViewer`). **However**, write operations need `objectCreator` or `objectAdmin` on the bucket; this must be added to the backend SA before P5 ships.

```bash
gcloud storage buckets add-iam-policy-binding gs://wwc-documents \
  --project=wwc-solutions \
  --member=serviceAccount:backend@wwc-solutions.iam.gserviceaccount.com \
  --role=roles/storage.objectAdmin
```

(`objectAdmin` includes `objectCreator` + `objectViewer` + delete; if delete is too broad, narrow to `objectCreator` + keep the existing `objectViewer`.)
