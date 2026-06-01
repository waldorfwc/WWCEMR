# Patient Portal P4 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the patient-facing Documents page. Aggregate consent PDFs (from P3), payment receipts (from P2), and a new procedure-specific instructions library (preop + postop PDFs in GCS).

**Architecture:** No new schema. New `app/services/surgery_documents.py` reads PDFs from `gs://wwc-documents/surgery-instructions/{procedure_classification}/{kind}.pdf` via the existing `google-cloud-storage` client. Two new portal endpoints (`GET /documents`, `GET /documents/instructions/{kind}`) and one frontend page (`Documents.jsx`) replace the P1 stub.

**Spec:** `docs/superpowers/specs/2026-06-01-patient-portal-p4-design.md`

**Prerequisite (already done):** `gs://wwc-documents` bucket exists in `us-east4`; backend service account `backend@wwc-solutions.iam.gserviceaccount.com` has `roles/storage.objectViewer`.

**Key facts about the existing code:**
- `google-cloud-storage` Python package is already installed (used by `app/services/gcs_documents.py` for the chart-document workflow). Reuse the same import.
- `Surgery.consent_envelopes` and `Surgery.payments` relationships already populate the data we surface.
- `Surgery.procedure_classification` is a String column — values include `office_d_and_c`, `robotic_tlh`, `leep`, `office_novasure`, etc.

---

## Task 1: Service helper — `fetch_instructions_pdf`

**Files:**
- Create: `backend/app/services/surgery_documents.py`
- Create: `backend/tests/test_surgery_documents.py`

- [ ] **Step 1: Write the failing test** at `backend/tests/test_surgery_documents.py`:

```python
"""Surgery documents service — GCS instructions library."""
from unittest.mock import patch, MagicMock

from app.services.surgery_documents import (
    fetch_instructions_pdf,
    INSTRUCTIONS_BUCKET,
)


def test_returns_pdf_bytes_when_object_exists():
    with patch("app.services.surgery_documents.storage.Client") as MockClient:
        blob = MagicMock()
        blob.exists.return_value = True
        blob.download_as_bytes.return_value = b"%PDF-test"
        bucket = MagicMock()
        bucket.blob.return_value = blob
        MockClient.return_value.bucket.return_value = bucket

        result = fetch_instructions_pdf("office_d_and_c", "preop")
        assert result == b"%PDF-test"
        bucket.blob.assert_called_with(
            "surgery-instructions/office_d_and_c/preop.pdf"
        )


def test_returns_none_when_object_missing():
    with patch("app.services.surgery_documents.storage.Client") as MockClient:
        blob = MagicMock()
        blob.exists.return_value = False
        bucket = MagicMock()
        bucket.blob.return_value = blob
        MockClient.return_value.bucket.return_value = bucket

        result = fetch_instructions_pdf("nonexistent_procedure", "preop")
        assert result is None


def test_returns_none_for_invalid_kind():
    """Defensive: caller should validate, but the service shouldn't crash."""
    result = fetch_instructions_pdf("office_d_and_c", "bogus")
    assert result is None


def test_returns_none_when_procedure_classification_blank():
    """Surgery with no procedure_classification → no library lookup."""
    assert fetch_instructions_pdf("", "preop") is None
    assert fetch_instructions_pdf(None, "preop") is None
```

- [ ] **Step 2: Run, confirm fail** (ImportError):

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_surgery_documents.py -v
```

- [ ] **Step 3: Create the service** at `backend/app/services/surgery_documents.py`:

```python
"""Patient-facing documents library — procedure-specific instruction PDFs
stored in GCS.

Bucket layout:
    gs://wwc-documents/surgery-instructions/{procedure_classification}/{kind}.pdf

where kind ∈ {"preop", "postop"} and procedure_classification is a value
from Surgery.procedure_classification (e.g. "office_d_and_c", "robotic_tlh").

When a PDF doesn't exist at the expected path, fetch_instructions_pdf
returns None and the calling endpoint surfaces a 404 → patient sees a
"Not available, call us" message in the portal.
"""
from __future__ import annotations

import logging
from typing import Optional

from google.cloud import storage

log = logging.getLogger(__name__)

INSTRUCTIONS_BUCKET = "wwc-documents"
VALID_KINDS = ("preop", "postop")


def fetch_instructions_pdf(
    procedure_classification: Optional[str],
    kind: str,
) -> Optional[bytes]:
    """Return PDF bytes for the requested instructions doc, or None when
    the procedure_classification is blank, the kind isn't recognized, or
    the GCS object doesn't exist. Soft-fails on storage errors — None
    return signals "not available" to the caller."""
    if not procedure_classification or kind not in VALID_KINDS:
        return None
    object_name = (
        f"surgery-instructions/{procedure_classification}/{kind}.pdf"
    )
    try:
        client = storage.Client()
        bucket = client.bucket(INSTRUCTIONS_BUCKET)
        blob = bucket.blob(object_name)
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as e:
        log.warning("instructions PDF fetch failed for %s: %s", object_name, e)
        return None
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/surgery_documents.py backend/tests/test_surgery_documents.py
git commit -m "feat(portal-p4): surgery_documents service — fetch_instructions_pdf from GCS"
```

---

## Task 2: GET /documents endpoint

**Files:**
- Modify: `backend/app/routers/patient_portal.py` — append handler
- Modify: `backend/tests/test_patient_portal_endpoints.py` — append 3 tests

- [ ] **Step 1: Failing tests** — append:

```python
def test_documents_aggregates_consents_and_receipts(client, db):
    from datetime import date as _d, datetime as _dt
    from decimal import Decimal
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    from app.models.stripe_payment import SurgeryPayment

    s = _seed_surgery(db)
    s.scheduled_date = _d(2026, 7, 1)
    s.procedure_classification = "office_d_and_c"

    # Signed consent + pending consent — only signed should appear
    t1 = ConsentTemplate(name="Office — Hysteroscopy D&C Consent",
                          boldsign_template_id="bs_t1",
                          procedure_match=[], facility_match=[])
    t2 = ConsentTemplate(name="LARC Form", boldsign_template_id="bs_t2",
                          procedure_match=[], facility_match=[])
    db.add_all([t1, t2]); db.flush()
    db.add_all([
        SurgeryConsentEnvelope(surgery_id=s.id, template_id=t1.id,
                                  boldsign_envelope_id="bs_doc_1",
                                  status="signed"),
        SurgeryConsentEnvelope(surgery_id=s.id, template_id=t2.id,
                                  boldsign_envelope_id="bs_doc_2",
                                  status="sent"),
    ])
    db.add(SurgeryPayment(
        surgery_id=s.id, status="paid",
        amount_requested=Decimal("250.00"),
        amount_paid=Decimal("250.00"),
        requested_by="staff",
        paid_at=_dt(2026, 5, 31, 12, 0),
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/documents",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Only the signed consent appears
    assert len(body["consents"]) == 1
    assert body["consents"][0]["template_name"] == "Office — Hysteroscopy D&C Consent"
    # The paid receipt appears
    assert len(body["receipts"]) == 1
    assert float(body["receipts"][0]["amount"]) == 250.0
    # Instructions structure exists with both kinds present (even if not yet uploaded)
    assert "instructions" in body
    assert "preop" in body["instructions"]
    assert "postop" in body["instructions"]


def test_documents_omits_unsigned_consents(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db)
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    db.add(SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc",
        status="sent",   # not yet signed
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/documents",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["consents"] == []


def test_documents_no_instructions_when_classification_blank(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    # procedure_classification stays None
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/documents",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    # When classification is blank, instructions section is null
    assert r.json()["instructions"] is None
```

- [ ] **Step 2: Run, confirm fail** (404 on endpoint).

- [ ] **Step 3: Add handler** to `backend/app/routers/patient_portal.py` (append at END of file):

```python
# ─── /{surgery_id}/documents ──────────────────────────────────────

@router.get("/{surgery_id}/documents")
def portal_documents(surgery_id: str, db: Session = Depends(get_db),
                       _: str = Depends(require_portal_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")

    # Consents — only show the ones the patient can actually download
    consents = []
    for env in (s.consent_envelopes or []):
        if (env.status or "") not in ("signed", "completed"):
            continue
        consents.append({
            "envelope_id":    str(env.id),
            "template_name":  env.template.name if env.template else "",
            "status":         env.status,
            "signed_at":      env.signed_at.isoformat() if env.signed_at else None,
        })

    # Receipts — only paid rows
    receipts = []
    for p in (s.payments or []):
        if p.status != "paid":
            continue
        receipts.append({
            "id":         str(p.id),
            "paid_at":    p.paid_at.isoformat() if p.paid_at else None,
            "amount":     str(p.amount_paid or 0),
        })

    # Instructions: structure stays present so the frontend can show both
    # rows. When the procedure has no classification, the whole section is
    # null and the frontend renders the "not available" message.
    if s.procedure_classification:
        instructions = {
            "preop":  {"available": None,   # Lazy: frontend probes on click
                       "kind": "preop"},
            "postop": {"available": None,
                       "kind": "postop"},
        }
    else:
        instructions = None

    return {
        "instructions": instructions,
        "consents":     consents,
        "receipts":     receipts,
    }
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p4): GET /documents — consent + receipts + instructions structure"
```

---

## Task 3: GET /documents/instructions/{kind} endpoint

**Files:**
- Modify: `backend/app/routers/patient_portal.py` — append streaming handler
- Modify: `backend/tests/test_patient_portal_endpoints.py` — append 3 tests

- [ ] **Step 1: Failing tests** — append:

```python
def test_instructions_pdf_returns_404_when_classification_blank(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/documents/instructions/preop",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_instructions_pdf_rejects_invalid_kind(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.procedure_classification = "office_d_and_c"
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/documents/instructions/bogus",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422


def test_instructions_pdf_streams_when_present(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.procedure_classification = "office_d_and_c"
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.surgery_documents.fetch_instructions_pdf",
                return_value=b"%PDF-test-bytes"):
        r = client.get(
            f"/api/patient/portal/{s.id}/documents/instructions/preop",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
    assert "pdf" in r.headers["content-type"].lower()
    assert "preop" in r.headers["content-disposition"].lower()
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add handler** to `backend/app/routers/patient_portal.py`:

```python
@router.get("/{surgery_id}/documents/instructions/{kind}")
def portal_documents_instructions(
    surgery_id: str,
    kind: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Stream a procedure-specific instructions PDF from GCS.
    kind ∈ {"preop", "postop"}. Returns 404 when the patient's
    procedure_classification has no doc in the library."""
    if kind not in ("preop", "postop"):
        raise HTTPException(status_code=422,
                              detail="kind must be 'preop' or 'postop'")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    if not s.procedure_classification:
        raise HTTPException(
            status_code=404,
            detail="Instructions for this procedure aren't online yet — "
                   "please call our office at 240-252-2140.",
        )
    from app.services.surgery_documents import fetch_instructions_pdf
    pdf_bytes = fetch_instructions_pdf(s.procedure_classification, kind)
    if pdf_bytes is None:
        raise HTTPException(
            status_code=404,
            detail="Instructions for this procedure aren't online yet — "
                   "please call our office at 240-252-2140.",
        )
    filename = f"{s.procedure_classification}_{kind}_instructions.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

`Response` should already be imported from `fastapi.responses` from P3 T5. If not, add it.

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p4): GET /documents/instructions/{kind} — stream PDF from GCS"
```

---

## Task 4: Frontend Documents page

**Files:**
- Rename: `frontend/src/pages/portal/stubs/DocumentsStub.jsx` → `frontend/src/pages/portal/Documents.jsx`
- Modify: `frontend/src/App.jsx` — update import + route element

- [ ] **Step 1: Rename.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git mv frontend/src/pages/portal/stubs/DocumentsStub.jsx frontend/src/pages/portal/Documents.jsx
```

- [ ] **Step 2: Update App.jsx.** Find:

```jsx
import DocumentsStub from './pages/portal/stubs/DocumentsStub'
```

Replace with:

```jsx
import Documents from './pages/portal/Documents'
```

Find the route element `<DocumentsStub />` and replace with `<Documents />`.

- [ ] **Step 3: Overwrite the renamed file** at `frontend/src/pages/portal/Documents.jsx`:

```jsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { portalApi } from '../../lib/portal-api'

function fmtMoney(v) {
  return `$${Number(v).toFixed(2)}`
}

function PdfDownloadButton({ url, filename, label }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(false)
  async function go() {
    setBusy(true); setErr(false)
    try {
      const r = await portalApi.get(url, { responseType: 'blob' })
      const blobUrl = URL.createObjectURL(r.data)
      const a = document.createElement('a')
      a.href = blobUrl
      a.download = filename
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(blobUrl)
    } catch (e) {
      setErr(true)
    } finally { setBusy(false) }
  }
  return (
    <div>
      <button onClick={go} disabled={busy} className="btn-secondary text-sm">
        {busy ? 'Loading…' : label}
      </button>
      {err && (
        <div className="text-xs text-red-600 mt-1">
          Not available — please call our office at{' '}
          <a href="tel:2402522140" className="underline">240-252-2140</a>.
        </div>
      )}
    </div>
  )
}

function InstructionsCard({ sid, instructions }) {
  if (instructions === null) {
    return (
      <section className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Instructions</h2>
        <p className="text-sm text-gray-600">
          Instructions for this procedure aren't online yet — please call our
          office at <a href="tel:2402522140" className="underline">240-252-2140</a>.
        </p>
      </section>
    )
  }
  return (
    <section className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Instructions</h2>
      <ul className="divide-y divide-gray-100">
        <li className="py-2 flex items-center justify-between">
          <span className="text-sm text-gray-800">Pre-op instructions</span>
          <PdfDownloadButton
            url={`/${sid}/documents/instructions/preop`}
            filename="preop_instructions.pdf"
            label="Download" />
        </li>
        <li className="py-2 flex items-center justify-between">
          <span className="text-sm text-gray-800">Post-op instructions</span>
          <PdfDownloadButton
            url={`/${sid}/documents/instructions/postop`}
            filename="postop_instructions.pdf"
            label="Download" />
        </li>
      </ul>
    </section>
  )
}

function ConsentDocsCard({ sid, consents }) {
  if (!consents?.length) {
    return (
      <section className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Consent forms</h2>
        <p className="text-sm text-gray-600">
          Signed consent forms will appear here once everyone has signed.
        </p>
      </section>
    )
  }
  return (
    <section className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Consent forms</h2>
      <ul className="divide-y divide-gray-100">
        {consents.map(c => (
          <li key={c.envelope_id}
              className="py-2 flex items-center justify-between gap-3">
            <span className="text-sm text-gray-800 truncate">
              {c.template_name}
            </span>
            <PdfDownloadButton
              url={`/${sid}/consent/signed-pdf/${c.envelope_id}`}
              filename={`${c.template_name.replace(/[^a-z0-9]/gi, '_')}.pdf`}
              label="Download" />
          </li>
        ))}
      </ul>
    </section>
  )
}

function ReceiptsCard({ receipts }) {
  if (!receipts?.length) {
    return (
      <section className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Receipts</h2>
        <p className="text-sm text-gray-600">
          Receipts for your payments will appear here.
        </p>
      </section>
    )
  }
  return (
    <section className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Receipts</h2>
      <ul className="divide-y divide-gray-100">
        {receipts.map(r => (
          <li key={r.id}
              className="py-2 flex items-center justify-between text-sm">
            <span>{(r.paid_at || '').slice(0, 10)}</span>
            <span className="text-gray-900">{fmtMoney(r.amount)}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function Documents() {
  const { sid } = useParams()
  const { data, isLoading } = useQuery({
    queryKey: ['portal-documents', sid],
    queryFn: () => portalApi.get(`/${sid}/documents`).then(r => r.data),
    staleTime: 30_000,
  })
  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-gray-900">Documents</h1>
      <InstructionsCard sid={sid} instructions={data.instructions} />
      <ConsentDocsCard sid={sid} consents={data.consents} />
      <ReceiptsCard receipts={data.receipts} />
    </div>
  )
}
```

- [ ] **Step 4: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -8
```

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/portal/Documents.jsx frontend/src/pages/portal/stubs/DocumentsStub.jsx \
        frontend/src/App.jsx
git commit -m "feat(portal-p4): Documents page (Instructions + Consents + Receipts)"
```

---

## Task 5: Drop "soon" from Documents nav

**Files:**
- Modify: `frontend/src/pages/portal/PortalShell.jsx`

- [ ] **Step 1: Update NAV.** Remove `comingSoon: true` from `documents`:

```jsx
const NAV = [
  { to: '',          label: 'Dashboard' },
  { to: 'payments',  label: 'Payments' },
  { to: 'schedule',  label: 'Schedule' },
  { to: 'consent',   label: 'Consent' },
  { to: 'documents', label: 'Documents' },
  { to: 'messages',  label: 'Messages',  comingSoon: true },
]
```

- [ ] **Step 2: Build check + commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/portal/PortalShell.jsx
git commit -m "feat(portal-p4): drop 'soon' from Documents nav item"
```

---

## Task 6: Smoke test in prod (manual)

Done after Tasks 1–5 are merged and deployed. I drive this.

- [ ] **Step 1: Upload a sample instructions PDF** to GCS so the smoke test has something to download:

```bash
# Use any existing PDF — even a one-liner LaTeX or pre-written sample.
echo "%PDF-1.4 test" > /tmp/sample_instructions.pdf
gcloud storage cp /tmp/sample_instructions.pdf \
  gs://wwc-documents/surgery-instructions/office_d_and_c/preop.pdf
```

(In production this would be the real WWC pre-op instructions PDF for D&Cs.)

- [ ] **Step 2: Push, build, deploy** backend `v44` + frontend `v_portal_p4`. No DB migration this time.

- [ ] **Step 3: Insert a test surgery** with `procedure_classification = "office_d_and_c"`, `version_id = 1` (avoid the optimistic-lock issue from P3 T8). Add a SurgeryConsentEnvelope with `status = "signed"` so the consents card has data. Add a SurgeryPayment with `status = "paid"` so the receipts card has data.

- [ ] **Step 4: Portal sign-in** (DOB + last4 → SMS → code). Confirm Documents nav no longer shows "· soon".

- [ ] **Step 5: Hit Documents page in browser.**
  - Instructions card shows two rows; click Pre-op Download → should stream the sample PDF.
  - Click Post-op Download → should show "Not available" inline (no postop.pdf uploaded).
  - Consents card shows the signed envelope with a Download button.
  - Receipts card shows the paid row with the amount.

- [ ] **Step 6: Cleanup** the test surgery + envelope + payment rows. Remove the sample PDF from GCS. Close Cloud SQL public IP.
