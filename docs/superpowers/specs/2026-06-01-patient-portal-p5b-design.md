# Patient Portal — P5b: FMLA

**Status:** Draft for review
**Author:** Claude Code, 2026-06-01
**Builds on:** P1–P5. Reuses upload infrastructure (P5), step-up SMS (P2), Stripe Checkout (P2).

## Goal

Let patients self-serve their FMLA paperwork end-to-end:

1. Upload their employer's blank FMLA form to the portal.
2. Pay the $25 processing fee (configurable) via Stripe Checkout, gated by an SMS step-up code.
3. See the status of their request as it moves `submitted → in_review → completed`.
4. Download the completed FMLA form (filled out by the office and uploaded back).

P5b is a thin layer over the existing infrastructure: GCS uploads via `surgery_uploads`, signed-URL downloads, Stripe Checkout, and step-up SMS are already shipped. The only genuinely new code is the FMLA-specific state transitions and a small Stripe extension to distinguish "patient balance" payments from "FMLA fee" payments.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Portal P5b — FMLA                                                │
│                                                                  │
│  GCS layout (reusing P5's bucket):                               │
│    gs://wwc-documents/surgery-uploads/{sid}/fmla_blank/...       │
│    gs://wwc-documents/surgery-uploads/{sid}/fmla_completed/...   │
│                                                                  │
│  GET /api/patient/portal/{sid}/fmla                              │
│        returns: { status, fee_amount, fee_paid, fee_paid_at,     │
│                   blank_uploads[], completed_uploads[] }         │
│                                                                  │
│  POST /api/patient/portal/{sid}/fmla/upload                      │
│        multipart — saves SurgeryDocument(kind="fmla_blank").     │
│        Auto-flips fmla_status to "submitted" if fee already paid.│
│                                                                  │
│  POST /api/patient/portal/{sid}/fmla/step-up                     │
│        SMS code with purpose="payment". Returns step_up_token.   │
│                                                                  │
│  POST /api/patient/portal/{sid}/fmla/checkout                    │
│        Body: { step_up_token, code }                             │
│        Verify code → create Stripe Checkout for $25 (or env var).│
│        Returns: { checkout_url }                                 │
│                                                                  │
│  GET /api/patient/portal/{sid}/fmla/completed/{doc_id}           │
│        Streams the completed FMLA PDF from GCS.                  │
│        Gated on: doc.kind=="fmla_completed" AND doc belongs to   │
│        this surgery.                                             │
│                                                                  │
│  Webhook:                                                        │
│    stripe checkout.session.completed for kind="fmla_fee"         │
│    → set fmla_fee_paid=True + auto-flip fmla_status to "submitted"│
│       if blank upload exists                                     │
│                                                                  │
│  Coordinator workflow (out of P5b scope — manual):               │
│    - Coordinator reviews fmla_blank upload via existing admin UI │
│    - Fills it out, uploads the completed PDF as fmla_completed   │
│      via a small admin-side endpoint (or SQL for now)            │
│    - Bumps fmla_status to "completed"                            │
└──────────────────────────────────────────────────────────────────┘
```

## New schema

### `Surgery` columns

```python
fmla_fee_paid             = Column(Boolean, default=False, nullable=False)
fmla_fee_paid_at          = Column(DateTime, nullable=True)
fmla_fee_stripe_session_id   = Column(String(100), nullable=True)
```

`Surgery.fmla_status` already exists (String(40), nullable). New status semantics:

| Status | Meaning |
|---|---|
| `NULL` / `""` | Patient hasn't requested FMLA |
| `"submitted"` | Patient uploaded blank + paid fee. Office to begin filling out. |
| `"in_review"` | Coordinator is actively filling out the form |
| `"completed"` | Completed form uploaded back; patient can download |

### `SurgeryPayment.kind` — discriminates payment types

```python
kind = Column(String(40), default="patient_balance", nullable=False)
```

Values:
- `"patient_balance"` — payment toward `patient_responsibility` (existing P2 behavior). Webhook bumps `Surgery.amount_paid`.
- `"fmla_fee"` — payment for FMLA processing. Webhook sets `Surgery.fmla_fee_paid = True`; does NOT bump `amount_paid`.

Existing rows default to `patient_balance` on migration so the existing webhook handler keeps working unchanged.

## Configurable fee amount

Env var on the backend Cloud Run service:
```
FMLA_FEE_CENTS=2500   # default $25.00
```

Backend reads this on every checkout creation; missing/invalid → fall back to 2500. Patient-facing UI fetches the fee in dollars from the GET /fmla response so it stays in sync if the env var changes.

## Status transition flow

```
START (fmla_status = NULL)
    │
    │ patient uploads fmla_blank
    ▼
fmla_status = NULL, has_blank_upload = True
    │
    │ patient pays $25 (Stripe webhook)
    ▼
fmla_fee_paid = True, fmla_status auto-flips → "submitted"
    │
    │ coordinator picks it up
    ▼
fmla_status = "in_review"
    │
    │ coordinator uploads fmla_completed, marks done
    ▼
fmla_status = "completed", has_completed_upload = True
    │
    │ patient downloads completed PDF
    ▼
DONE
```

The auto-flip to `"submitted"` happens at two trigger points:
- Patient's upload handler: if fee already paid, flip status now.
- Stripe webhook for FMLA fee: if blank upload exists, flip status now.

This way the patient can do upload-then-pay OR pay-then-upload (less common) and either reaches `"submitted"` once both are done.

## New backend endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET  | `/api/patient/portal/{sid}/fmla` | portal | Status + fee + uploads list |
| POST | `/api/patient/portal/{sid}/fmla/upload` | portal | Multipart upload of blank FMLA |
| POST | `/api/patient/portal/{sid}/fmla/step-up` | portal | Sends SMS code for fee payment |
| POST | `/api/patient/portal/{sid}/fmla/checkout` | portal | Code verify + Stripe Checkout |
| GET  | `/api/patient/portal/{sid}/fmla/completed/{doc_id}` | portal | Stream completed FMLA PDF |

## New service helpers

`backend/app/services/stripe_payments.py` (extension):

```python
def create_fmla_checkout_session(db, surgery, *, fee_cents: int,
                                    actor: str) -> SurgeryPayment:
    """Create a Stripe Checkout session for an FMLA processing fee.
    Persists a SurgeryPayment row with kind='fmla_fee' so the webhook
    routes status updates correctly."""
```

`_handle_checkout_completed` (existing webhook handler) gets a small branch:

```python
if pay.kind == "fmla_fee":
    s.fmla_fee_paid = True
    s.fmla_fee_paid_at = datetime.utcnow()
    s.fmla_fee_stripe_session_id = session_id
    # Auto-flip status if blank upload already there
    has_blank = any(d.kind == "fmla_blank" for d in s.documents)
    if has_blank and (s.fmla_status or "") in ("", None):
        s.fmla_status = "submitted"
else:
    # existing patient_balance path: bump amount_paid
    s.amount_paid = (s.amount_paid or 0) + amount_paid
```

## Frontend additions

### Documents page: 5th card

The Documents page (P4 + P5) gains an `FmlaCard` component below `ClearanceCard`. Unlike Clearance, FMLA is shown for ALL surgeries (every patient might need work leave). State-aware rendering:

**Not started:**
> If you need FMLA documentation for work, upload your employer's blank form and pay the $25 processing fee. We'll fill it out within 5 business days.
>
> [Choose file] [Upload]
>
> Pay $25 fee  *(disabled until upload exists)*

**Upload done, payment pending:**
> ✓ Form received. Pay the $25 processing fee to submit your request.
>
> Your uploads: my_fmla_form.pdf
>
> [Pay $25] *(triggers step-up SMS → code entry → Stripe Checkout)*

**Submitted (both done):**
> ✓ Submitted. We're filling out your form and will have it ready within 5 business days.
>
> Your uploads: my_fmla_form.pdf

**In review:**
> Your form is in review.
>
> Your uploads: my_fmla_form.pdf

**Completed:**
> ✓ Completed. Your completed FMLA paperwork is ready.
>
> [Download completed FMLA]

The pay button reuses the SMS-step-up + 6-digit-code-entry pattern from P2's Payments page — extract that into a shared component to avoid duplication.

### Dashboard milestone

The existing FMLA milestone row (added in P3 T-something) already shows when `fmla_status` is set. P5b makes it actively appear in `_next_action` priority. No new code on the dashboard side.

## What's NOT in P5b (defer)

- **Coordinator admin UI** for marking `fmla_status="in_review"`/`"completed"` and uploading the completed form via a portal-side endpoint. P5b assumes the coordinator does this via SQL or the existing surgery admin UI. P6 adds a small staff-side endpoint.
- **Multiple FMLA submissions per surgery** — e.g., patient resubmits a different form. P5b allows multiple `fmla_blank` uploads but uses the latest one. Coordinator decides which to fill.
- **Refunds** if the patient cancels or the office can't complete the FMLA. Manual via Stripe Dashboard for now.
- **Email + SMS notifications** when status changes. Could reuse the patient_email and patient_sms infrastructure but it's noise for v1.

## Open questions

1. **What if the patient pays but never uploads?** The status stays NULL/empty even though `fmla_fee_paid=True`. The portal UI shows "Pay $25 first" — but they already paid. This is a corner case that requires staff intervention. P5b handles it with a different UI state: "Payment received — please upload your form to complete your request."

2. **What if the patient uploads multiple blank forms?** The latest one wins for the coordinator's review purposes. All `fmla_blank` documents are persisted (audit trail) and patient sees them all listed.

3. **What if the patient uploads → pays → cancels?** Patient self-cancel isn't supported in P5b. Coordinator can refund via Stripe Dashboard and reset `fmla_status` + `fmla_fee_paid` manually.

## Risks

- **Stripe webhook race condition**: Patient pays, webhook fires. If the upload doesn't exist YET (e.g., they paid first via a stale UI state), the auto-flip to `"submitted"` doesn't happen. Mitigation: the upload handler also checks `fmla_fee_paid` and flips status if both are now true. Both paths converge.
- **Multipart upload for FMLA inherits all the validation from P5**: 10 MB max, PDF/JPEG/PNG/HEIC only, magic-byte sniff. No new attack surface.
- **Step-up token reuse across purposes**: P2's pattern reuses `PatientPortalAuthCode` with `purpose="payment"`. The route-level boundary (this endpoint only checks codes via `auth.verify_code`) prevents a step-up code from being repurposed elsewhere. Same risk profile as P2.

## Tech stack

Same as P5. New schema lives in `Surgery` and `SurgeryPayment` (no new tables). New endpoints in `patient_portal.py`. Stripe extension in `stripe_payments.py`. One new frontend component (`FmlaCard`) plus a shared `StepUpPayFlow` extracted from P2's Payments page if not already extracted.
