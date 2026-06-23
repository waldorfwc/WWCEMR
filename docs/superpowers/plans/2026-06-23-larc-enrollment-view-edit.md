# LARC Enrollment View & Edit From the Card — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let reception preview the enrollment form before sending (with blank-field warnings), and after sending view/download the live BoldSign PDF and edit the form in BoldSign's embedded editor — all from the enrollment card.

**Architecture:** Three thin backend endpoints on the existing `/larc` router plus three service functions in `enrollment_sender.py` (a preview resolver reusing existing data sources, and two BoldSign client wrappers: `createEmbeddedEditUrl` and document download). Editing happens in place on the same `boldsign_envelope_id`, so the webhook/fax pipeline is untouched. Frontend adds Preview/View/Edit affordances to the enrollment card.

**Tech Stack:** FastAPI + SQLAlchemy + httpx (backend), React + @tanstack/react-query + axios (frontend), BoldSign REST API, pytest.

**Spec:** `docs/superpowers/specs/2026-06-23-larc-enrollment-view-edit-design.md`

---

## Key existing facts (verified)

- BoldSign client pattern in `backend/app/services/larc/enrollment_sender.py`:
  - `API_BASE = "https://api.boldsign.com"`, `_http()` returns `httpx.Client(base_url=API_BASE, timeout=20.0, headers={"X-API-KEY": _api_key(), ...})`.
  - `_api_key()` and `_is_configured()` already exist (used by `void_live_envelopes_for_assignment`).
  - `LarcEnrollmentError(Exception)` is the sender's error class; the router maps it to HTTP 409.
  - `_TEMPLATE_SPECS: dict[str, _TemplateSpec]` maps template_id → spec with `.nice_name`, `.roles`, `.field_builder`.
  - `now_utc_naive` from `app.utils.dt`; `get_all_practice_settings` is `from app.services.practice_settings import get_all as get_all_practice_settings`.
- Router `backend/app/routers/larc.py`: `router = APIRouter(prefix="/larc", ...)`. Helpers: `_load_assignment(db, id)` (404s if missing), `_latest_envelope_dict(a)`. Auth dep: `requires_tier(Module.LARC, Tier.WORK)`. Audit: `log_action(...)` and `log_audit(...)`. `from fastapi.responses import StreamingResponse, Response` is already imported.
- Envelope fetch-by-id pattern: `db.query(LarcEnrollmentEnvelope).filter(LarcEnrollmentEnvelope.id == envelope_id).first()`.
- Model `LarcEnrollmentEnvelope`: has `boldsign_envelope_id`, `boldsign_template_id`, `status` (values incl. `sent`, `partially_signed`, `signed`, `voided`, `declined`, `faxed`). `LarcAssignment`: `source_flow` ("pharmacy_order"), `patient_email`, `patient_first_name/last_name`, `patient_dob`, `patient_address/city/state/zip`, `primary_insurance`, `insurance_policy_no/group_no`, `inserting_provider_email/name/npi`, `app_name/npi`, `device_type_id`, `pharmacy_id`.
- Frontend `LarcAssignment.jsx`: `import api, { fmt } from '../utils/api'` (axios, baseURL `/api`); `invalidateLarcLists(qc, id)`; components `EnrollmentSentBody` and `EnrollmentEnvelopeStatus`. `fmt.date()` renders MM/DD/YYYY. lucide icons already imported include `FileText`, `Eye`, `Edit3`.
- Tests: `backend/tests/` uses `client_factory(user=u)` (seed a `User`, override `get_current_user`), and mocks BoldSign by patching the module `_http` to return a `MagicMock` whose `__enter__.return_value.post/get` returns a fake response. Reference: `backend/tests/test_boldsign_envelopes.py`.

---

## Task 1: Backend — enrollment preview resolver + endpoint

**Files:**
- Modify: `backend/app/services/larc/enrollment_sender.py`
- Modify: `backend/app/routers/larc.py`
- Test: `backend/tests/test_larc_enrollment_view_edit.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_larc_enrollment_view_edit.py`:

```python
import pytest
from app.models.user import User
from app.models.larc import LarcAssignment, LarcEnrollmentEnvelope
from app.services.larc.enrollment_sender import resolve_enrollment_preview


def _work_user(db):
    u = User(email="work@waldorfwomenscare.com", display_name="Work", is_super_admin=True)
    db.add(u); db.commit()
    return u


def _pharmacy_assignment(db, **over):
    a = LarcAssignment(
        chart_number="12345",
        patient_name="Jane Doe",
        source_flow="pharmacy_order",
        status="in_progress",
        patient_first_name="Jane",
        patient_last_name="Doe",
        patient_email="jane@example.com",
        primary_insurance="Aetna",
        inserting_provider_email="dr@waldorfwomenscare.com",
        inserting_provider_name="Dr. Smith",
        inserting_provider_npi="1234567890",
    )
    for k, v in over.items():
        setattr(a, k, v)
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_resolve_preview_full_data_no_blanks(db):
    a = _pharmacy_assignment(db)
    out = resolve_enrollment_preview(db, a)
    labels = {f["label"]: f for f in out["fields"]}
    assert labels["Patient Name"]["value"] == "Jane Doe"
    assert labels["Patient Name"]["blank"] is False
    assert labels["Primary Insurance"]["value"] == "Aetna"
    assert out["sendable"] is True


def test_resolve_preview_flags_blanks(db):
    a = _pharmacy_assignment(db, primary_insurance=None, inserting_provider_npi=None)
    out = resolve_enrollment_preview(db, a)
    assert "Primary Insurance" in out["blanks"]
    assert "Inserting Provider NPI" in out["blanks"]


def test_resolve_preview_not_sendable_without_patient_email(db):
    a = _pharmacy_assignment(db, patient_email=None)
    out = resolve_enrollment_preview(db, a)
    assert out["sendable"] is False
    assert "Patient Email" in out["blanks"]


def test_preview_endpoint_shape_and_tier(client_factory, db):
    u = _work_user(db)
    a = _pharmacy_assignment(db)
    client = client_factory(user=u)
    r = client.get(f"/api/larc/assignments/{a.id}/enrollment/preview")
    assert r.status_code == 200
    body = r.json()
    assert "fields" in body and "blanks" in body and "sendable" in body


def test_preview_endpoint_rejects_non_pharmacy_flow(client_factory, db):
    u = _work_user(db)
    a = _pharmacy_assignment(db, source_flow="in_stock")
    client = client_factory(user=u)
    r = client.get(f"/api/larc/assignments/{a.id}/enrollment/preview")
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_larc_enrollment_view_edit.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_enrollment_preview'`.

- [ ] **Step 3: Implement the resolver**

In `backend/app/services/larc/enrollment_sender.py`, add near the other module-level helpers (after `_TEMPLATE_SPECS`). It reads the same source data the field builders use (assignment columns + practice settings), so the preview reflects what will actually be sent without reverse-mapping opaque BoldSign field IDs:

```python
def _fmt_date_mdY(d) -> str:
    return d.strftime("%m/%d/%Y") if d else ""


def resolve_enrollment_preview(db: Session, a: LarcAssignment) -> dict:
    """Human-readable summary of the values that would populate the
    enrollment form, with blank-field flags. Drives the card's Preview.

    Reads the same sources the field builders use (assignment columns +
    PracticeConfig) so the preview matches what gets sent, without
    reverse-mapping BoldSign's opaque field IDs."""
    s = get_all_practice_settings(db)

    provider_name = a.inserting_provider_name or (
        f"{s.get('provider_first_name') or ''} {s.get('provider_last_name') or ''}".strip())
    provider_npi = a.inserting_provider_npi or s.get("provider_npi") or ""
    app_name = a.app_name or s.get("app_name") or ""
    app_npi = a.app_npi or s.get("app_npi") or ""

    spec = _resolve_template_spec_for_assignment(db, a)

    rows = [
        ("Patient Name", a.patient_name or
            f"{a.patient_first_name or ''} {a.patient_last_name or ''}".strip()),
        ("Patient DOB", _fmt_date_mdY(a.patient_dob)),
        ("Patient Email", a.patient_email or ""),
        ("Patient Address", " ".join(p for p in [
            a.patient_address, a.patient_city,
            f"{a.patient_state or ''} {a.patient_zip or ''}".strip()] if p)),
        ("Primary Insurance", a.primary_insurance or ""),
        ("Policy #", a.insurance_policy_no or ""),
        ("Group #", a.insurance_group_no or ""),
        ("Inserting Provider", provider_name),
        ("Inserting Provider NPI", provider_npi),
        ("APP Name", app_name),
        ("APP NPI", app_npi),
        ("Practice Name", s.get("practice_name") or ""),
        ("Practice Fax", s.get("practice_fax") or ""),
    ]
    fields = [{"label": lbl, "value": val, "blank": (val is None or val == "")}
              for lbl, val in rows]
    blanks = [f["label"] for f in fields if f["blank"]]

    # Hard preconditions the send path also enforces.
    sendable = True
    if not a.patient_email:
        sendable = False
        if "Patient Email" not in blanks:
            blanks.append("Patient Email")
    if a.device_type_id is None or spec is None:
        sendable = False

    return {
        "template": spec.nice_name if spec else None,
        "fields": fields,
        "blanks": blanks,
        "sendable": sendable,
    }
```

Then DRY the template resolution: find the inline logic in `send_enrollment_envelope` that maps the assignment's device type to a `_TemplateSpec` (it looks up the device type's `enrollment_form_template` / template id, then `_TEMPLATE_SPECS[template_id]`). Extract it verbatim into a module-level helper and call it from both `send_enrollment_envelope` and `resolve_enrollment_preview`:

```python
def _resolve_template_spec_for_assignment(db: Session, a: LarcAssignment):
    """Return the _TemplateSpec for this assignment's device type, or None
    if the device type has no configured enrollment template. (Extracted
    from send_enrollment_envelope so the preview resolver shares it.)"""
    # <move the existing device-type -> template_id lookup here, returning
    #  _TEMPLATE_SPECS.get(template_id) or None>
```

(Implementer: replace the inline block in `send_enrollment_envelope` with a call to this helper so there is exactly one copy. Match the existing lookup exactly — do not invent a new attribute name.)

- [ ] **Step 4: Add the endpoint**

In `backend/app/routers/larc.py`, after the `send_enrollment` handler:

```python
@router.get("/assignments/{assignment_id}/enrollment/preview")
def enrollment_preview(assignment_id: str,
                       db: Session = Depends(get_db),
                       current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Resolved enrollment-form field values + blank-field warnings, so
    reception can confirm nothing sends blank. Works before or after send."""
    a = _load_assignment(db, assignment_id)
    if a.source_flow != "pharmacy_order":
        raise HTTPException(status_code=400,
                            detail="Enrollment only applies to pharmacy_order flow")
    from app.services.larc.enrollment_sender import resolve_enrollment_preview
    return resolve_enrollment_preview(db, a)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_larc_enrollment_view_edit.py -q`
Expected: PASS (5 tests). Then run the existing enrollment tests to confirm the template-resolution extraction didn't regress: `pytest tests/ -k enrollment -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/larc/enrollment_sender.py backend/app/routers/larc.py backend/tests/test_larc_enrollment_view_edit.py
git commit -m "feat(larc): enrollment form preview resolver + endpoint"
```

---

## Task 2: Backend — embedded edit URL

**Files:**
- Modify: `backend/app/services/larc/enrollment_sender.py`
- Modify: `backend/app/routers/larc.py`
- Test: `backend/tests/test_larc_enrollment_view_edit.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_larc_enrollment_view_edit.py`:

```python
from unittest.mock import MagicMock, patch


def _envelope(db, a, status="sent", doc_id="bs_doc_1"):
    env = LarcEnrollmentEnvelope(
        assignment_id=a.id,
        boldsign_template_id="9af154d6-0bc7-43f6-bf94-175b7daf27e6",
        boldsign_envelope_id=doc_id,
        status=status,
    )
    db.add(env); db.commit(); db.refresh(env)
    return env


def test_edit_url_returns_url_when_editable(client_factory, db, monkeypatch):
    monkeypatch.setenv("BOLDSIGN_API_KEY", "xxx")
    u = _work_user(db); a = _pharmacy_assignment(db); env = _envelope(db, a, status="sent")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"editFormUrl": "https://app.boldsign.com/edit/abc"}
    fake = MagicMock()
    fake.__enter__.return_value.post.return_value = resp
    client = client_factory(user=u)
    with patch("app.services.larc.enrollment_sender._http", return_value=fake), \
         patch("app.services.larc.enrollment_sender._is_configured", return_value=True):
        r = client.get(f"/api/larc/envelopes/{env.id}/edit-url"
                       "?redirect=https://app.waldorfwomenscare.com/larc/assignments/x")
    assert r.status_code == 200
    assert r.json()["url"] == "https://app.boldsign.com/edit/abc"


def test_edit_url_409_when_fully_signed(client_factory, db):
    u = _work_user(db); a = _pharmacy_assignment(db); env = _envelope(db, a, status="signed")
    client = client_factory(user=u)
    r = client.get(f"/api/larc/envelopes/{env.id}/edit-url")
    assert r.status_code == 409
    assert r.json()["detail"]["detail"] == "not_editable"


def test_edit_url_409_when_boldsign_rejects(client_factory, db, monkeypatch):
    monkeypatch.setenv("BOLDSIGN_API_KEY", "xxx")
    u = _work_user(db); a = _pharmacy_assignment(db); env = _envelope(db, a, status="sent")
    resp = MagicMock(status_code=400, text="cannot edit")
    fake = MagicMock()
    fake.__enter__.return_value.post.return_value = resp
    client = client_factory(user=u)
    with patch("app.services.larc.enrollment_sender._http", return_value=fake), \
         patch("app.services.larc.enrollment_sender._is_configured", return_value=True):
        r = client.get(f"/api/larc/envelopes/{env.id}/edit-url")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "boldsign_rejected"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_larc_enrollment_view_edit.py -k edit_url -q`
Expected: FAIL — endpoint 404 (route not defined) / import error.

- [ ] **Step 3: Implement the service function + exception**

In `backend/app/services/larc/enrollment_sender.py`, add near the top-level error class:

```python
class EnrollmentNotEditable(Exception):
    """BoldSign refused an embedded-edit URL for this document (already
    completed / declined / revoked, or otherwise locked)."""


# Hosts a redirect URL may target after editing, to avoid open redirect.
# Suffix match; extend via env if a new domain is added.
_ALLOWED_REDIRECT_SUFFIXES = tuple(
    h.strip() for h in os.environ.get(
        "ALLOWED_REDIRECT_HOSTS",
        "waldorfwomenscare.com,run.app,localhost",
    ).split(",") if h.strip()
)


def _safe_redirect(url: Optional[str]) -> Optional[str]:
    """Return url only if it's an https/http URL whose host ends with an
    allowed suffix; otherwise None (BoldSign uses its default)."""
    if not url:
        return None
    from urllib.parse import urlparse
    p = urlparse(url)
    if p.scheme not in ("https", "http") or not p.hostname:
        return None
    if any(p.hostname == sfx or p.hostname.endswith("." + sfx) or p.hostname == sfx
           for sfx in _ALLOWED_REDIRECT_SUFFIXES):
        return url
    return None


def create_embedded_edit_url(env: LarcEnrollmentEnvelope, *,
                             redirect_url: Optional[str] = None) -> str:
    """Get a BoldSign embedded edit URL for an existing/sent document.
    Raises EnrollmentNotEditable if BoldSign rejects the request."""
    if not _is_configured():
        raise LarcEnrollmentError("BoldSign API key not configured")
    body = {
        "viewOption": "PreparePage",
        "showToolbar": True,
        "showSaveButton": True,
        "showSendButton": True,
        "showPreviewButton": True,
    }
    rd = _safe_redirect(redirect_url)
    if rd:
        body["redirectUrl"] = rd
    with _http() as c:
        r = c.post("/v1/document/createEmbeddedEditUrl",
                   params={"documentId": env.boldsign_envelope_id},
                   json=body)
    if r.status_code >= 300:
        raise EnrollmentNotEditable(
            f"BoldSign edit-url {r.status_code}: {r.text[:200]}")
    data = r.json()
    url = data.get("editFormUrl") or data.get("url") or data.get("embeddedEditUrl")
    if not url:
        raise EnrollmentNotEditable(f"BoldSign response missing edit url: {data!r}")
    return url
```

- [ ] **Step 4: Add the endpoint**

In `backend/app/routers/larc.py`, after the `refax_envelope` handler:

```python
# Envelope statuses where an embedded edit still makes sense. Once fully
# signed / faxed / voided / declined, editing is closed -> 409.
_EDITABLE_ENVELOPE_STATUSES = {"sent", "partially_signed"}


@router.get("/envelopes/{envelope_id}/edit-url")
def enrollment_edit_url(envelope_id: str,
                        redirect: Optional[str] = Query(default=None),
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Issue a BoldSign embedded edit URL so reception can edit a sent
    envelope in place. 409 if the envelope is no longer editable."""
    env = (db.query(LarcEnrollmentEnvelope)
             .filter(LarcEnrollmentEnvelope.id == envelope_id).first())
    if env is None:
        raise HTTPException(status_code=404, detail="envelope not found")
    if env.status not in _EDITABLE_ENVELOPE_STATUSES:
        raise HTTPException(status_code=409,
                            detail={"detail": "not_editable", "reason": env.status})
    from app.services.larc.enrollment_sender import (
        create_embedded_edit_url, EnrollmentNotEditable,
    )
    try:
        url = create_embedded_edit_url(env, redirect_url=redirect)
    except EnrollmentNotEditable:
        raise HTTPException(status_code=409,
                            detail={"detail": "not_editable", "reason": "boldsign_rejected"})
    by = current_user.get("email") or "system"
    log_action(db, "enrollment_edit_url_issued", "larc_enrollment_envelope",
               actor=current_user, resource_id=str(env.id),
               description=f"Issued BoldSign edit URL for envelope {env.id}",
               defer_commit=False)
    return {"url": url}
```

(Implementer: match `log_action`'s real signature as used elsewhere in this file — copy an existing call's keyword shape; the above mirrors the manual router's usage. If `log_action` here uses `log_audit(...)` style instead, use that.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_larc_enrollment_view_edit.py -k edit_url -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/larc/enrollment_sender.py backend/app/routers/larc.py backend/tests/test_larc_enrollment_view_edit.py
git commit -m "feat(larc): embedded edit URL for sent enrollment envelopes"
```

---

## Task 3: Backend — view/download document PDF

**Files:**
- Modify: `backend/app/services/larc/enrollment_sender.py`
- Modify: `backend/app/routers/larc.py`
- Test: `backend/tests/test_larc_enrollment_view_edit.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_document_endpoint_streams_pdf(client_factory, db, monkeypatch):
    monkeypatch.setenv("BOLDSIGN_API_KEY", "xxx")
    u = _work_user(db); a = _pharmacy_assignment(db); env = _envelope(db, a, status="signed")
    resp = MagicMock(status_code=200, content=b"%PDF-1.7 fake")
    fake = MagicMock()
    fake.__enter__.return_value.get.return_value = resp
    client = client_factory(user=u)
    with patch("app.services.larc.enrollment_sender._http", return_value=fake), \
         patch("app.services.larc.enrollment_sender._is_configured", return_value=True):
        r = client.get(f"/api/larc/envelopes/{env.id}/document")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content == b"%PDF-1.7 fake"


def test_document_endpoint_502_on_boldsign_error(client_factory, db, monkeypatch):
    monkeypatch.setenv("BOLDSIGN_API_KEY", "xxx")
    u = _work_user(db); a = _pharmacy_assignment(db); env = _envelope(db, a, status="sent")
    resp = MagicMock(status_code=422, text="not ready")
    fake = MagicMock()
    fake.__enter__.return_value.get.return_value = resp
    client = client_factory(user=u)
    with patch("app.services.larc.enrollment_sender._http", return_value=fake), \
         patch("app.services.larc.enrollment_sender._is_configured", return_value=True):
        r = client.get(f"/api/larc/envelopes/{env.id}/document")
    assert r.status_code == 502
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/test_larc_enrollment_view_edit.py -k document -q`
Expected: FAIL — route 404.

- [ ] **Step 3: Implement the download function**

In `backend/app/services/larc/enrollment_sender.py`:

```python
def download_envelope_pdf(env: LarcEnrollmentEnvelope) -> tuple[bytes, str]:
    """Fetch the current PDF for an envelope from BoldSign (works at any
    status). Returns (pdf_bytes, filename). Raises LarcEnrollmentError on
    failure."""
    if not _is_configured():
        raise LarcEnrollmentError("BoldSign API key not configured")
    with _http() as c:
        r = c.get("/v1/document/download",
                  params={"documentId": env.boldsign_envelope_id})
    if r.status_code >= 300:
        raise LarcEnrollmentError(
            f"BoldSign download {r.status_code}: {r.text[:200]}")
    short = (env.boldsign_envelope_id or "doc")[:8]
    return r.content, f"enrollment-{short}.pdf"
```

- [ ] **Step 4: Add the endpoint**

In `backend/app/routers/larc.py`, after `enrollment_edit_url`:

```python
@router.get("/envelopes/{envelope_id}/document")
def enrollment_document(envelope_id: str,
                        db: Session = Depends(get_db),
                        current_user: dict = Depends(requires_tier(Module.LARC, Tier.WORK))):
    """Stream the enrollment envelope's current PDF inline for viewing."""
    env = (db.query(LarcEnrollmentEnvelope)
             .filter(LarcEnrollmentEnvelope.id == envelope_id).first())
    if env is None:
        raise HTTPException(status_code=404, detail="envelope not found")
    from app.services.larc.enrollment_sender import (
        download_envelope_pdf, LarcEnrollmentError,
    )
    try:
        pdf, filename = download_envelope_pdf(env)
    except LarcEnrollmentError:
        raise HTTPException(status_code=502, detail="document_unavailable")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_larc_enrollment_view_edit.py -q`
Expected: PASS (all tests in the file, ~10).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/larc/enrollment_sender.py backend/app/routers/larc.py backend/tests/test_larc_enrollment_view_edit.py
git commit -m "feat(larc): view/download enrollment document PDF endpoint"
```

---

## Task 4: Frontend — Preview modal + button (before & after send)

**Files:**
- Create: `frontend/src/components/larc/EnrollmentPreviewModal.jsx`
- Modify: `frontend/src/pages/LarcAssignment.jsx`

- [ ] **Step 1: Create the preview modal component**

`frontend/src/components/larc/EnrollmentPreviewModal.jsx`:

```jsx
import { useQuery } from '@tanstack/react-query'
import { X, AlertTriangle } from 'lucide-react'
import api from '../../utils/api'

export default function EnrollmentPreviewModal({ assignmentId, onClose }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['larc-enrollment-preview', assignmentId],
    queryFn: () => api.get(`/larc/assignments/${assignmentId}/enrollment/preview`)
      .then(r => r.data),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
         onClick={onClose}>
      <div className="bg-white rounded-lg shadow-xl w-[460px] max-h-[80vh] overflow-auto p-4"
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold">Enrollment Form Preview</h3>
          <button onClick={onClose}><X size={16} /></button>
        </div>
        {isLoading && <div className="text-[12px] text-gray-500">Loading…</div>}
        {error && <div className="text-[12px] text-danger">Couldn't load preview.</div>}
        {data && (
          <>
            {data.blanks?.length > 0 && (
              <div className="flex items-start gap-1.5 text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1.5 mb-2">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <span>{data.blanks.length} field{data.blanks.length > 1 ? 's' : ''} will
                  send blank: {data.blanks.join(', ')}.</span>
              </div>
            )}
            {!data.sendable && (
              <div className="text-[11px] text-danger mb-2">
                Patient email is required before sending.
              </div>
            )}
            <table className="w-full text-[12px]">
              <tbody>
                {data.fields.map(f => (
                  <tr key={f.label} className="border-b border-border-subtle">
                    <td className="py-1 pr-2 text-gray-500 align-top w-[45%]">{f.label}</td>
                    <td className={'py-1 ' + (f.blank ? 'text-amber-700 italic' : 'text-gray-900')}>
                      {f.blank ? '— blank —' : f.value}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Wire the Preview button into the before-send card**

In `frontend/src/pages/LarcAssignment.jsx`, add the import near the top:

```jsx
import EnrollmentPreviewModal from '../components/larc/EnrollmentPreviewModal'
```

In `EnrollmentSentBody`, add modal state and a Preview button next to Send. Replace the button row:

```jsx
      <div className="flex gap-2">
        <button className="btn-primary text-[11px]"
                onClick={() => send.mutate()}
                disabled={send.isPending}>
          {send.isPending ? 'Sending…' : 'Send Enrollment via BoldSign'}
        </button>
        <button className="btn-secondary text-[11px]"
                onClick={() => setShowPreview(true)}>
          Preview Form
        </button>
      </div>
```

Add `const [showPreview, setShowPreview] = useState(false)` with the other hooks, and before the component's closing `</div>`:

```jsx
      {showPreview && (
        <EnrollmentPreviewModal assignmentId={a.id} onClose={() => setShowPreview(false)} />
      )}
```

- [ ] **Step 3: Build the frontend to verify it compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds, no missing-import errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/larc/EnrollmentPreviewModal.jsx frontend/src/pages/LarcAssignment.jsx
git commit -m "feat(larc): enrollment form preview modal before send"
```

---

## Task 5: Frontend — View Form + Edit Form on the sent card

**Files:**
- Modify: `frontend/src/pages/LarcAssignment.jsx`

- [ ] **Step 1: Add View/Edit/Preview controls to `EnrollmentEnvelopeStatus`**

In `frontend/src/pages/LarcAssignment.jsx`, update `EnrollmentEnvelopeStatus` to accept the query client and render action buttons. Add at the top of the component:

```jsx
function EnrollmentEnvelopeStatus({ a, env }) {
  const qc = useQueryClient()
  const [showPreview, setShowPreview] = useState(false)
  const [editErr, setEditErr] = useState(null)
  const editable = env.status === 'sent' || env.status === 'partially_signed'

  const openEdit = async () => {
    setEditErr(null)
    try {
      const redirect = window.location.href
      const r = await api.get(`/larc/envelopes/${env.id}/edit-url`,
                              { params: { redirect } })
      window.open(r.data.url, '_blank', 'noopener')
    } catch (e) {
      if (e?.response?.status === 409) {
        setEditErr('This form can no longer be edited because signing has progressed. Void and resend instead.')
      } else {
        setEditErr('Could not open the editor. Try again.')
      }
    }
  }
  // ... existing steps/fax computation unchanged ...
```

Refetch when the user returns from the BoldSign tab (so edits/signatures show):

```jsx
  useEffect(() => {
    const onFocus = () => invalidateLarcLists(qc, a.id)
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [qc, a.id])
```

Then add the buttons block just before the component's closing `</div>` (after the void/declined lines):

```jsx
      <div className="flex flex-wrap gap-2 pt-1">
        <button className="btn-secondary text-[11px]"
                onClick={() => window.open(`/api/larc/envelopes/${env.id}/document`, '_blank', 'noopener')}>
          View Form
        </button>
        <button className="btn-secondary text-[11px]" onClick={() => setShowPreview(true)}>
          Preview
        </button>
        {editable && (
          <button className="btn-secondary text-[11px]" onClick={openEdit}>
            Edit Form
          </button>
        )}
      </div>
      {editErr && <div className="text-[11px] text-danger">{editErr}</div>}
      {showPreview && (
        <EnrollmentPreviewModal assignmentId={a.id} onClose={() => setShowPreview(false)} />
      )}
```

Ensure `useEffect` is in the React import at the top of the file (it already imports `useState, useMemo, useEffect`).

- [ ] **Step 2: Build the frontend**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/LarcAssignment.jsx
git commit -m "feat(larc): view + edit enrollment form from the sent card"
```

---

## Task 6: Docs — LARC manual section

**Files:**
- Modify: `backend/app/services/manual_seed.py`

- [ ] **Step 1: Add a manual section to `LARC_MANUAL_SECTIONS`**

In `backend/app/services/manual_seed.py`, find `LARC_MANUAL_SECTIONS = [` and add a new section tuple with a unique slug `enrollment-view-edit` and the next sort_order after the existing enrollment-related section (read the list to pick the right number; use a value that places it just after the enrollment-send content):

```python
    ("enrollment-view-edit", "Viewing & Editing the Enrollment Form", <next_order>, """\
Once you've filled in the patient, insurance, and provider details, you can
check and manage the BoldSign enrollment form right from the assignment card.

**Preview before sending:** Click **Preview Form** to see exactly what will be
filled in. Any blank fields are flagged at the top — fix the missing data
(Practice Profile or patient demographics) before sending so the form never
goes out empty.

**After sending:**
- **View Form** opens the current PDF in a new tab — including partially-signed
  state — so you can confirm what each signer sees.
- **Edit Form** opens the form in BoldSign's editor so you can correct fields and
  re-send in place. This is available until signing completes; once everyone has
  signed (or the form is voided/declined), Edit Form disappears — at that point
  void the envelope and send a fresh one.
""")
```

(Implementer: choose `<next_order>` by reading the existing `LARC_MANUAL_SECTIONS` sort_order values; keep them ascending. Match the Title Case + MM/DD/YYYY conventions already used in the file.)

- [ ] **Step 2: Validate the seed parses**

Run:
```bash
cd backend && python -c "from app.services import manual_seed as m; \
slugs=[s[0] for s in m.LARC_MANUAL_SECTIONS]; \
assert 'enrollment-view-edit' in slugs and len(slugs)==len(set(slugs)); \
print('ok', slugs)"
```
Expected: prints `ok [...]` including `enrollment-view-edit`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/manual_seed.py
git commit -m "docs(manual): LARC enrollment view & edit section"
```

---

## Final verification (after all tasks)

- [ ] `cd backend && pytest -q` → full suite green (no regressions from the template-resolution extraction).
- [ ] `cd frontend && npm run build` → succeeds.
- [ ] Dispatch a final code reviewer over the whole diff.
- [ ] Then use `superpowers:finishing-a-development-branch`.

## Self-review notes

- **Spec coverage:** preview-before-send (T1+T4), view/download after send (T3+T5), embedded edit after send with editability guard + graceful 409 (T2+T5), manual (T6). All covered.
- **DRY:** template-spec resolution extracted into one helper shared by sender + preview (T1 step 3).
- **YAGNI:** no local form-field storage, no send-flow rewrite, no webhook/fax changes.
- **Naming consistency:** `resolve_enrollment_preview`, `create_embedded_edit_url`, `download_envelope_pdf`, `EnrollmentNotEditable`, endpoints `/assignments/{id}/enrollment/preview`, `/envelopes/{id}/edit-url`, `/envelopes/{id}/document` — used identically across backend tasks and frontend calls.
- **Open-redirect guard:** `_safe_redirect` validates the BoldSign redirect host against an allowlist.
