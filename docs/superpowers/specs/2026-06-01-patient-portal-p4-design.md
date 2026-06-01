# Patient Portal — P4: Documents

**Status:** Draft for review
**Author:** Claude Code, 2026-06-01
**Builds on:** P1 (auth + shell + dashboard), P2 (payments + scheduling), P3 (consent)

## Goal

Replace the Documents stub with a single page where the patient sees **every document associated with their surgery** in one place:

1. **Consent forms** — signed PDFs (links to the P3 download endpoint)
2. **Payment receipts** — links to Stripe-hosted receipts
3. **Procedure-specific instructions** — preop and postop PDFs from the practice's library

P4 is intentionally **downloads-only**. Uploads (FMLA forms, clearance documents, EKG) land in P5 with their own workflow-specific UI (fee payment, image preview, etc.).

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Portal P4 — Documents                                            │
│                                                                   │
│  GET /api/patient/portal/{sid}/documents                          │
│        returns:                                                   │
│          {                                                        │
│            instructions: { preop: {...}, postop: {...} } | null,  │
│            consents: [ {template_name, status, can_download,      │
│                          envelope_id}, ... ],                      │
│            receipts: [ {paid_at, amount, hosted_receipt_url} ],    │
│          }                                                         │
│                                                                   │
│  GET /api/patient/portal/{sid}/documents/instructions/{kind}      │
│        kind: "preop" | "postop"                                    │
│        streams a PDF from GCS based on                             │
│        surgery.procedure_classification (e.g. office_d_and_c).     │
│        Returns 404 when the library doesn't have a doc for that   │
│        procedure (frontend shows a "Not available — call us" hint │
│        instead of a broken link).                                  │
│                                                                   │
│  Frontend                                                         │
│    /portal/s/:sid/documents — <PortalDocuments /> replaces stub   │
│      ├── <InstructionsCard /> — preop + postop PDFs by procedure  │
│      ├── <ConsentDocsCard />  — list of signed consents (downloads) │
│      └── <ReceiptsCard />     — list of paid payments + receipts   │
└──────────────────────────────────────────────────────────────────┘
```

No new schema. Consents come from `SurgeryConsentEnvelope` (P3), receipts from `SurgeryPayment` (P2), instructions from a GCS bucket with a fixed naming convention.

## Instructions library

### Naming convention

```
gs://wwc-documents/surgery-instructions/{procedure_classification}/{kind}.pdf

where:
  procedure_classification ∈ Surgery.procedure_classification (e.g. "office_d_and_c",
                              "robotic_tlh", "leep", etc.)
  kind ∈ {"preop", "postop"}
```

So a patient with `procedure_classification = "office_d_and_c"` gets:
- `gs://wwc-documents/surgery-instructions/office_d_and_c/preop.pdf`
- `gs://wwc-documents/surgery-instructions/office_d_and_c/postop.pdf`

### Bucket setup

If the bucket doesn't exist:
```
gsutil mb -p wwc-solutions -l us-east4 gs://wwc-documents
gsutil iam ch serviceAccount:backend@wwc-solutions.iam.gserviceaccount.com:objectViewer gs://wwc-documents
```

The backend Cloud Run service account already has Cloud Storage access from earlier work (verify before the spec ships).

### Uploading library docs

Out of scope for P4 code. The user uploads PDFs via gcloud directly when they have copy ready:
```
gsutil cp office_d_and_c_preop.pdf gs://wwc-documents/surgery-instructions/office_d_and_c/preop.pdf
```

A small admin UI for managing the library can be added later if needed.

### Missing-doc fallback

If `procedure_classification` is null or no PDF exists at the expected path, the `GET /documents/instructions/{kind}` endpoint returns 404, and the `GET /documents` endpoint returns `instructions: null` for that surgery. The frontend shows:

> "Instructions for this procedure aren't online yet — please call our office at 240-252-2140."

This is the patient-friendly fallback for procedures whose library docs haven't been uploaded.

## Documents portal page

### Three cards, each with its own data source

```
┌─ Documents ───────────────────────────────────────────┐
│                                                       │
│  ┌─ Instructions ─────────────────────────┐          │
│  │ Pre-op instructions       [Download]   │          │
│  │ Post-op instructions      [Download]   │          │
│  │ — or "Not available, call us" message  │          │
│  └────────────────────────────────────────┘          │
│                                                       │
│  ┌─ Consent forms ───────────────────────┐           │
│  │ Office — Hysteroscopy D&C  ✓ signed   │           │
│  │                          [Download]    │           │
│  │ — or "Not yet completed" if status     │           │
│  │   isn't signed                         │           │
│  └────────────────────────────────────────┘          │
│                                                       │
│  ┌─ Receipts ─────────────────────────────┐          │
│  │ 2026-05-31  $250.00  [View on Stripe]  │          │
│  └────────────────────────────────────────┘          │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### State variants

- **No data anywhere** (surgery scheduled but nothing else) → "Documents will appear here as your surgery progresses."
- **Instructions missing only** → show the consent + receipts cards, replace instructions card with the fallback message.
- **Pre-op surgery (no receipts yet)** → omit receipts card.
- **Post-op (everything done)** → all three cards present, all rows show ✓.

## Receipts data source

`SurgeryPayment` rows already exist (P2). The new endpoint reuses the same data as `GET /payments` but filters to `status="paid"` and exposes:
- `paid_at`
- `amount_paid`
- `stripe_payment_intent_id` (already on the model) — used to build a `hosted_receipt_url`

Stripe doesn't expose hosted receipt URLs directly via the API — they're shown to the patient at checkout-success time. To surface a downloadable receipt after the fact, we use Stripe's Payment Intent → latest charge → receipt_url chain. The first version can punt this and just show the `paid_at` + amount with no link; a follow-up adds the live receipt URL by calling Stripe's API per row.

**P4 decision:** show paid_at + amount only. Link-to-Stripe-receipt is a P4b improvement that fetches the receipt URL lazily when the row is rendered.

## Consent data source

Same `SurgeryConsentEnvelope` rows P3 reads. The Documents page only surfaces envelopes where `can_download == True` (status is `signed` or `completed`). Pending envelopes belong on the Consent page, not the Documents page.

## New backend endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/patient/portal/{sid}/documents` | portal | Aggregated docs payload |
| GET | `/api/patient/portal/{sid}/documents/instructions/{kind}` | portal | Streams a PDF from GCS |

`{kind}` is restricted to `"preop"` or `"postop"` — any other value returns 422.

## New service helper

```python
# backend/app/services/surgery_documents.py
def fetch_instructions_pdf(procedure_classification: str, kind: str) -> Optional[bytes]:
    """Returns PDF bytes or None when no doc is found. Reads from
    gs://wwc-documents/surgery-instructions/{procedure_classification}/{kind}.pdf
    using the existing GCS client."""
```

## New frontend page

```
frontend/src/pages/portal/Documents.jsx
  ├── <InstructionsCard /> — two download buttons (preop + postop)
  ├── <ConsentDocsCard />  — filters consent.envelopes by can_download
  └── <ReceiptsCard />     — list of paid SurgeryPayment rows
```

`<InstructionsCard />` and `<ConsentDocsCard />` reuse the `DownloadButton` pattern from P3 (axios blob fetch so the Authorization header is sent, then trigger a download via `<a download>`).

## What's NOT in P4 (defer)

- **Uploads.** Anything the patient *gives* the office (FMLA forms, clearance, EKG, photos) → P5.
- **Live Stripe receipt URLs.** P4 shows paid_at + amount only. Hosted receipt link is P4b.
- **Admin UI for managing the instructions library.** Out of scope. User uploads via gcloud for now.
- **Document versioning.** If instructions PDFs change, the GCS object is overwritten — no history.
- **Per-patient customized PDFs.** All patients with the same `procedure_classification` get the same library doc. If a patient needs a customized doc, the coordinator emails it directly.

## Open questions

1. **Where do receipts live UX-wise — Documents page or Payments page?** Currently they're on Payments (history list, P2). P4 surfaces them on Documents too. Argument: patients think of receipts as documents; both surfaces are fine. Default: show on both.

2. **Should `procedure_classification` map to a friendlier display name in the instructions card?** Currently it's a code like `office_d_and_c`. Patient won't see this code (the card just says "Pre-op instructions"), so no.

3. **Audit logging for downloads?** Each instructions PDF download could be logged as a `PatientDocumentDownload` row for compliance. Probably overkill for a small practice; defer until a compliance request makes it necessary.

## Risks

- **GCS object missing.** Realistic — until the library is populated, every surgery hits the 404 fallback. The fallback message is patient-friendly and not a blocker.
- **GCS read latency.** Streaming through Cloud Run adds ~100ms vs. direct GCS access. For a few-MB PDF this is fine.
- **Bucket access.** The Cloud Run service account needs `objectViewer` on `gs://wwc-documents`. Confirm before deploy; the bucket setup section above includes the IAM command.

## Tech stack

Identical to P1/P2/P3. New service helper uses the `google-cloud-storage` Python library (already a transitive dep through other GCP services in this codebase). New frontend page replaces the existing `DocumentsStub.jsx` placeholder.
