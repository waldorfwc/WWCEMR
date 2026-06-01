# Patient Portal P3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the patient-facing Consent screen. Consent envelopes auto-create when the patient claims a slot (downstream of payment + schedule via the P2 chain), and the patient signs in-portal via BoldSign embedded sign URLs.

**Architecture:** No new schema — all consent state lives in the existing `SurgeryConsentEnvelope` rows. The auto-send is a one-line addition to `claim_slot_for_patient` (the shared service from P2 T3), so both magic-link and portal scheduling paths get it. Four new portal endpoints (`/consent`, `/consent/resend`, `/consent/sign-link/{env}`, `/consent/signed-pdf/{env}`) and one frontend page (`Consent.jsx`) replace the P1 stub.

**Spec:** `docs/superpowers/specs/2026-05-31-patient-portal-p3-design.md`

**Key facts about the existing code (don't relitigate):**
- `app/services/boldsign_envelopes.py:165` defines `send_consent_envelopes(db, s, *, sent_by="system", ignore_warnings=False) -> dict`. It indexes existing rows and **skips templates that already have a non-failed envelope**, so calling it twice is safe — won't duplicate.
- `app/services/boldsign_envelopes.py` also exposes `get_envelope_status(envelope_id)`, `reconcile_surgery_consent(db, s)`, and `sync_surgery_envelopes(db, s)`.
- BoldSign API base is `https://api.boldsign.com`, auth via `X-API-KEY: <BOLDSIGN_API_KEY>`. Headers helper exists at `boldsign_envelopes._headers()`.
- Embedded sign link: `GET /v1/document/getEmbeddedSignLink?documentId=<id>&signerEmail=<email>` (verified live earlier today).
- Signed PDF download: `GET /v1/document/download?documentId=<id>`.
- `Surgery.consent_envelopes` is a relationship to `SurgeryConsentEnvelope`. Status values: `pending`, `sent`, `delivered`, `signed`, `declined`, `voided`, `expired`, `failed`.

---

## Task 1: Auto-send consent on slot claim

**Files:**
- Modify: `backend/app/services/surgery_self_schedule.py` — add the BoldSign side effect
- Modify: `backend/tests/test_surgery_self_schedule.py` — append 2 tests

The existing `claim_slot_for_patient` ends with two soft-fail side effects (calendar sync, confirmation email). Add a third for BoldSign.

- [ ] **Step 1: Failing tests** — append:

```python
def test_claim_triggers_boldsign_send(db):
    from unittest.mock import patch
    s = _seed_s(db); bd = _seed_bd(db)
    with patch("app.services.surgery_self_schedule.upsert_event_for_surgery"), \
         patch("app.services.surgery_self_schedule._send_surgery_confirmation_email"), \
         patch("app.services.boldsign_envelopes.send_consent_envelopes") as mock_send:
        claim_slot_for_patient(
            db, s, block_day_id=str(bd.id),
            start_time_str="08:00", sent_by="patient:portal",
        )
    mock_send.assert_called_once()
    # Confirm the sent_by is propagated so the audit trail captures who scheduled
    _, kwargs = mock_send.call_args
    assert kwargs.get("sent_by") == "patient:portal"


def test_claim_succeeds_when_boldsign_send_fails(db):
    """BoldSign outage must not block the booking."""
    from unittest.mock import patch
    s = _seed_s(db); bd = _seed_bd(db)
    with patch("app.services.surgery_self_schedule.upsert_event_for_surgery"), \
         patch("app.services.surgery_self_schedule._send_surgery_confirmation_email"), \
         patch("app.services.boldsign_envelopes.send_consent_envelopes",
                side_effect=Exception("BoldSign 503")):
        result = claim_slot_for_patient(
            db, s, block_day_id=str(bd.id),
            start_time_str="08:00", sent_by="patient:portal",
        )
    # The slot was still claimed
    assert result["start_time"] == "08:00"
    db.refresh(s)
    assert s.scheduled_date == bd.block_date
```

- [ ] **Step 2: Run, confirm fail** (the side effect call isn't there yet):

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_surgery_self_schedule.py -v -k "boldsign"
```

- [ ] **Step 3: Add the side effect** to `backend/app/services/surgery_self_schedule.py`. Find the existing soft-fail block at the end of `claim_slot_for_patient`:

```python
    try:
        upsert_event_for_surgery(db, surgery)
    except Exception as e:
        log.warning("calendar sync failed: %s", e)
    try:
        _send_surgery_confirmation_email(db, surgery, slot, sent_by=sent_by)
    except Exception as e:
        log.warning("confirmation email failed: %s", e)
```

Append a third try block:

```python
    try:
        # Soft-fail: a BoldSign outage doesn't block the booking. Patient
        # can retry from portal Consent page via POST /consent/resend.
        from app.services.boldsign_envelopes import send_consent_envelopes
        send_consent_envelopes(db, surgery, sent_by=sent_by)
    except Exception as e:
        log.warning("consent envelope send failed: %s", e)
```

The lazy import avoids a circular-import risk (boldsign_envelopes imports from app.models and app.services.consent_template_matcher; if either ever needs to import from surgery_self_schedule the lazy import keeps the cycle from biting at startup).

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/surgery_self_schedule.py backend/tests/test_surgery_self_schedule.py
git commit -m "feat(portal-p3): auto-send consent envelopes when patient claims a slot"
```

---

## Task 2: GET /api/patient/portal/{sid}/consent

**Files:**
- Modify: `backend/app/routers/patient_portal.py` — append handler
- Modify: `backend/tests/test_patient_portal_endpoints.py` — append tests

- [ ] **Step 1: Failing tests** — append:

```python
def test_consent_returns_empty_when_unscheduled(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.procedures = [{"cpt": "58558", "description": "Hysteroscopy with D&C"}]
    s.selected_facility = "office"
    # scheduled_date is None
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/consent",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scheduled_date"] is None
    assert body["envelopes"] == []
    assert body["can_resend"] is False  # not scheduled yet


def test_consent_returns_envelopes_when_present(client, db):
    from datetime import date as _d
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db)
    s.scheduled_date = _d(2026, 7, 1)
    s.procedures = [{"cpt": "58558", "description": "Hysteroscopy with D&C"}]
    s.selected_facility = "office"
    t = ConsentTemplate(name="Office — Hysteroscopy D&C Consent",
                          boldsign_template_id="bs_t1",
                          procedure_match=["hysteroscopy with d&c"],
                          facility_match=["office"])
    db.add(t); db.flush()
    env = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_1",
        status="sent",
    )
    db.add(env); db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/consent",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["envelopes"]) == 1
    assert body["envelopes"][0]["template_name"] == "Office — Hysteroscopy D&C Consent"
    assert body["envelopes"][0]["status"] == "sent"
    assert body["envelopes"][0]["can_sign"] is True   # status is "sent"
    assert body["envelopes"][0]["can_download"] is False
    assert body["all_complete"] is False
    assert body["can_resend"] is True  # scheduled


def test_consent_all_complete_when_every_envelope_signed(client, db):
    from datetime import date as _d
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db)
    s.scheduled_date = _d(2026, 7, 1)
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    db.add(SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_2",
        status="signed",
    ))
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/consent",
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["all_complete"] is True
    assert body["envelopes"][0]["can_sign"] is False
    assert body["envelopes"][0]["can_download"] is True
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add handler** to `backend/app/routers/patient_portal.py` (append at end of file):

```python
# ─── /{surgery_id}/consent ────────────────────────────────────────

from app.models.surgery import SurgeryConsentEnvelope


def _envelope_dict(env: SurgeryConsentEnvelope) -> dict:
    status = env.status or ""
    return {
        "id":               str(env.id),
        "template_name":    env.template.name if env.template else "",
        "boldsign_envelope_id": env.boldsign_envelope_id,
        "status":           status,
        "sent_at":          env.sent_at.isoformat() if env.sent_at else None,
        "signed_at":        env.signed_at.isoformat() if env.signed_at else None,
        "can_sign":         status in ("sent", "delivered", "pending"),
        "can_download":     status in ("signed", "completed"),
    }


@router.get("/{surgery_id}/consent")
def portal_consent(surgery_id: str, db: Session = Depends(get_db),
                     _: str = Depends(require_portal_token)):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    envs = [_envelope_dict(e) for e in (s.consent_envelopes or [])]
    all_complete = bool(envs) and all(
        (e["status"] in ("signed", "completed")) for e in envs
    )
    return {
        "scheduled_date": s.scheduled_date.isoformat() if s.scheduled_date else None,
        "envelopes": envs,
        "all_complete": all_complete,
        "can_resend": s.scheduled_date is not None,
    }
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p3): GET /consent — envelope status + resend gate"
```

---

## Task 3: POST /api/patient/portal/{sid}/consent/resend

**Files:**
- Modify: `backend/app/routers/patient_portal.py`
- Modify: `backend/tests/test_patient_portal_endpoints.py`

- [ ] **Step 1: Failing tests** — append:

```python
def test_resend_blocked_when_not_scheduled(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.procedure_classification = "office_d_and_c"
    db.commit()
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/consent/resend",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 409
    assert "schedule" in r.text.lower()


def test_resend_calls_send_consent_envelopes(client, db):
    from datetime import date as _d
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.scheduled_date = _d(2026, 7, 1)
    s.procedures = [{"cpt": "58558", "description": "Hysteroscopy with D&C"}]
    s.selected_facility = "office"
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.boldsign_envelopes.send_consent_envelopes",
                return_value={"sent": [], "skipped": [],
                              "unmatched_procedures": [], "warnings": []}) as mock:
        r = client.post(f"/api/patient/portal/{s.id}/consent/resend",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    mock.assert_called_once()
    _, kwargs = mock.call_args
    assert kwargs.get("sent_by") == "patient:portal:resend"
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add handler** to `backend/app/routers/patient_portal.py`:

```python
@router.post("/{surgery_id}/consent/resend")
def portal_consent_resend(surgery_id: str, db: Session = Depends(get_db),
                            _: str = Depends(require_portal_token)):
    """Manual retry of consent envelope creation. Used when auto-send at
    slot-claim time failed (e.g., BoldSign outage). Requires a scheduled
    date — patient must have completed the schedule flow first."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.scheduled_date is None:
        raise HTTPException(
            status_code=409,
            detail="Please pick your surgery date first; consent forms "
                   "are created when you schedule.",
        )
    from app.services.boldsign_envelopes import (
        send_consent_envelopes, BoldSignEnvelopeError,
    )
    try:
        send_consent_envelopes(db, s, sent_by="patient:portal:resend")
    except BoldSignEnvelopeError as e:
        # Service-level rejection (e.g. no matching templates) — surface
        # the message to the patient.
        raise HTTPException(status_code=409, detail=str(e))
    # Re-fetch the consent payload so the frontend has fresh state.
    return portal_consent(surgery_id, db=db, _="ignored")
```

If `portal_consent` already exists as a function in the file (from T2), `return portal_consent(...)` reuses it directly. The `_="ignored"` works because the dependency was already validated by THIS endpoint's own `Depends(require_portal_token)` — FastAPI does not re-invoke deps on a direct call.

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p3): POST /consent/resend — manual retry when scheduled"
```

---

## Task 4: GET /api/patient/portal/{sid}/consent/sign-link/{envelope_id}

**Files:**
- Modify: `backend/app/services/boldsign_envelopes.py` — add `get_embedded_sign_link()` service helper
- Modify: `backend/app/routers/patient_portal.py` — add endpoint
- Modify: `backend/tests/test_patient_portal_endpoints.py`

The endpoint must only ever return the **patient** signing URL — never the surgeon's or witness's.

- [ ] **Step 1: Failing tests** — append:

```python
def test_sign_link_returns_url_for_patient_email(client, db):
    from datetime import date as _d
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db); s.email = "patient@example.com"
    s.scheduled_date = _d(2026, 7, 1)
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    env = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_999", status="sent",
    )
    db.add(env); db.commit(); db.refresh(env)
    token = issue_portal_token(s)
    with patch("app.services.boldsign_envelopes.get_embedded_sign_link",
                return_value="https://app.boldsign.com/signing/abc"):
        r = client.get(
            f"/api/patient/portal/{s.id}/consent/sign-link/{env.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["sign_url"].startswith("https://app.boldsign.com/")


def test_sign_link_rejects_envelope_from_different_surgery(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s1 = _seed_surgery(db, cell="+12405551111", dob=date(1990, 1, 1))
    s2 = _seed_surgery(db, cell="+12405552222", dob=date(1991, 2, 2))
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    env = SurgeryConsentEnvelope(
        surgery_id=s2.id, template_id=t.id,
        boldsign_envelope_id="bs_other", status="sent",
    )
    db.add(env); db.commit(); db.refresh(env)
    # Token is for s1; envelope belongs to s2.
    token = issue_portal_token(s1)
    r = client.get(
        f"/api/patient/portal/{s1.id}/consent/sign-link/{env.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404  # envelope not found for this surgery
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add service helper** to `backend/app/services/boldsign_envelopes.py`. Find the existing API helpers and append:

```python
def get_embedded_sign_link(envelope_id: str, signer_email: str) -> str:
    """Fetch a BoldSign embedded sign URL for a specific signer email on
    a document. Used by the patient portal — the calling endpoint MUST
    pass the patient's email (surgery.email) and never the surgeon's or
    witness's email.

    BoldSign embedded sign URLs are short-lived (~5 min per their docs),
    so callers should fetch on-demand when the patient clicks Sign now,
    not at page load.
    """
    if not _is_configured():
        raise BoldSignEnvelopeError("BoldSign API key not configured")
    with _http() as c:
        r = c.get(
            "/v1/document/getEmbeddedSignLink",
            params={"documentId": envelope_id, "signerEmail": signer_email},
        )
    if r.status_code >= 300:
        raise BoldSignEnvelopeError(
            f"BoldSign sign-link fetch failed: {r.status_code} {r.text[:200]}"
        )
    body = r.json()
    url = body.get("signLink") or body.get("SignLink") or body.get("signUrl")
    if not url:
        raise BoldSignEnvelopeError(
            f"BoldSign response missing signLink: {body!r}"
        )
    return url
```

- [ ] **Step 4: Add endpoint** to `backend/app/routers/patient_portal.py`:

```python
@router.get("/{surgery_id}/consent/sign-link/{envelope_id}")
def portal_consent_sign_link(
    surgery_id: str,
    envelope_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Return a BoldSign embedded sign URL for the patient role on this
    envelope. Hardcodes signer_email to surgery.email so the endpoint
    cannot be tricked into returning the surgeon's or witness's link."""
    env = (db.query(SurgeryConsentEnvelope)
              .filter(SurgeryConsentEnvelope.id == envelope_id,
                       SurgeryConsentEnvelope.surgery_id == surgery_id)
              .first())
    if env is None:
        raise HTTPException(status_code=404, detail="envelope not found")
    if not env.boldsign_envelope_id:
        raise HTTPException(status_code=409,
                              detail="Envelope was not sent via BoldSign.")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not (s.email or "").strip():
        raise HTTPException(status_code=409,
                              detail="No email on file — call our office.")
    from app.services.boldsign_envelopes import (
        get_embedded_sign_link, BoldSignEnvelopeError,
    )
    try:
        url = get_embedded_sign_link(env.boldsign_envelope_id, s.email)
    except BoldSignEnvelopeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"sign_url": url}
```

- [ ] **Step 5: Run, confirm pass.**

- [ ] **Step 6: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/boldsign_envelopes.py backend/app/routers/patient_portal.py \
        backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p3): GET /consent/sign-link — BoldSign embedded URL for patient role"
```

---

## Task 5: GET /api/patient/portal/{sid}/consent/signed-pdf/{envelope_id}

**Files:**
- Modify: `backend/app/services/boldsign_envelopes.py` — add `download_signed_pdf()` helper
- Modify: `backend/app/routers/patient_portal.py` — streaming endpoint
- Modify: `backend/tests/test_patient_portal_endpoints.py`

- [ ] **Step 1: Failing test** — append:

```python
def test_signed_pdf_rejects_unsigned_envelope(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db)
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    env = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_x", status="sent",   # not signed yet
    )
    db.add(env); db.commit(); db.refresh(env)
    token = issue_portal_token(s)
    r = client.get(
        f"/api/patient/portal/{s.id}/consent/signed-pdf/{env.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409
    assert "not yet" in r.text.lower() or "not signed" in r.text.lower()


def test_signed_pdf_streams_when_signed(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import ConsentTemplate, SurgeryConsentEnvelope
    s = _seed_surgery(db)
    t = ConsentTemplate(name="X", boldsign_template_id="bs_x",
                          procedure_match=[], facility_match=[])
    db.add(t); db.flush()
    env = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_y", status="signed",
    )
    db.add(env); db.commit(); db.refresh(env)
    token = issue_portal_token(s)
    with patch("app.services.boldsign_envelopes.download_signed_pdf",
                return_value=b"%PDF-fake-bytes"):
        r = client.get(
            f"/api/patient/portal/{s.id}/consent/signed-pdf/{env.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")
    assert "pdf" in r.headers["content-type"].lower()
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add service helper** to `backend/app/services/boldsign_envelopes.py`:

```python
def download_signed_pdf(envelope_id: str) -> bytes:
    """Fetch the signed PDF for an envelope from BoldSign. Returns raw
    bytes. Should only be called for envelopes with status=signed or
    completed; BoldSign returns 404/422 for unsigned documents."""
    if not _is_configured():
        raise BoldSignEnvelopeError("BoldSign API key not configured")
    with _http() as c:
        r = c.get(
            "/v1/document/download",
            params={"documentId": envelope_id},
        )
    if r.status_code >= 300:
        raise BoldSignEnvelopeError(
            f"BoldSign PDF download failed: {r.status_code} {r.text[:200]}"
        )
    return r.content
```

- [ ] **Step 4: Add endpoint** to `backend/app/routers/patient_portal.py`:

```python
from fastapi.responses import Response


@router.get("/{surgery_id}/consent/signed-pdf/{envelope_id}")
def portal_consent_signed_pdf(
    surgery_id: str,
    envelope_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Stream the signed PDF from BoldSign for download.
    Only available when envelope.status is signed or completed."""
    env = (db.query(SurgeryConsentEnvelope)
              .filter(SurgeryConsentEnvelope.id == envelope_id,
                       SurgeryConsentEnvelope.surgery_id == surgery_id)
              .first())
    if env is None:
        raise HTTPException(status_code=404, detail="envelope not found")
    if (env.status or "") not in ("signed", "completed"):
        raise HTTPException(
            status_code=409,
            detail="Document is not yet signed by all parties.",
        )
    if not env.boldsign_envelope_id:
        raise HTTPException(status_code=409,
                              detail="Envelope was not sent via BoldSign.")
    from app.services.boldsign_envelopes import (
        download_signed_pdf, BoldSignEnvelopeError,
    )
    try:
        pdf_bytes = download_signed_pdf(env.boldsign_envelope_id)
    except BoldSignEnvelopeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # Use a friendly filename based on the template name.
    label = "consent"
    if env.template and env.template.name:
        # Strip non-alphanum to keep filename safe.
        label = "".join(c if c.isalnum() else "_"
                          for c in env.template.name)[:60].strip("_") or "consent"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{label}.pdf"'},
    )
```

If `Response` is already imported via `from fastapi import ...`, don't duplicate the import.

- [ ] **Step 5: Run, confirm pass.**

- [ ] **Step 6: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/boldsign_envelopes.py backend/app/routers/patient_portal.py \
        backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p3): GET /consent/signed-pdf — stream signed PDF on demand"
```

---

## Task 6: Frontend — Consent page

**Files:**
- Rename: `frontend/src/pages/portal/stubs/ConsentStub.jsx` → `frontend/src/pages/portal/Consent.jsx`
- Modify: `frontend/src/App.jsx` — update import + route element

- [ ] **Step 1: Rename.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git mv frontend/src/pages/portal/stubs/ConsentStub.jsx frontend/src/pages/portal/Consent.jsx
```

- [ ] **Step 2: Update App.jsx.** Change:

```jsx
import ConsentStub from './pages/portal/stubs/ConsentStub'
```

To:

```jsx
import Consent from './pages/portal/Consent'
```

And replace the route element `<ConsentStub />` with `<Consent />`.

- [ ] **Step 3: Write the page** at `frontend/src/pages/portal/Consent.jsx`:

```jsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams, Link } from 'react-router-dom'
import { portalApi } from '../../lib/portal-api'

function EmptyState({ scheduledDate, sid }) {
  if (!scheduledDate) {
    return (
      <div className="bg-white rounded-lg shadow p-4 text-sm text-gray-600">
        Once you've paid and picked a surgery date, your consent forms will
        appear here automatically.
        <div className="mt-3 flex gap-2">
          <Link to={`/portal/s/${sid}/payments`} className="btn-secondary text-sm">
            Go to Payments
          </Link>
          <Link to={`/portal/s/${sid}/schedule`} className="btn-secondary text-sm">
            Go to Schedule
          </Link>
        </div>
      </div>
    )
  }
  // Scheduled but no envelopes — auto-send failed
  return null
}

function EnvelopeRow({ env, sid }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function signNow() {
    setBusy(true); setErr('')
    try {
      const { data } = await portalApi.get(
        `/${sid}/consent/sign-link/${env.id}`,
      )
      window.location.assign(data.sign_url)
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not start signing.')
      setBusy(false)
    }
  }

  const statusBadge =
    env.status === 'signed' || env.status === 'completed'
      ? 'bg-green-100 text-green-700'
      : env.status === 'declined' || env.status === 'voided' || env.status === 'failed'
      ? 'bg-red-100 text-red-700'
      : 'bg-amber-100 text-amber-700'

  return (
    <li className="py-3 flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-gray-900 truncate">
          {env.template_name || 'Consent form'}
        </div>
        <div className="text-xs text-gray-500 mt-0.5">
          <span className={`inline-block px-2 py-0.5 rounded ${statusBadge}`}>
            {env.status}
          </span>
          {env.sent_at && <span className="ml-2">sent {env.sent_at.slice(0, 10)}</span>}
        </div>
        {err && <div className="text-xs text-red-600 mt-1">{err}</div>}
      </div>
      <div className="flex gap-2">
        {env.can_sign && (
          <button onClick={signNow} disabled={busy}
                   className="btn-primary text-sm">
            {busy ? 'Opening…' : 'Sign now'}
          </button>
        )}
        {env.can_download && (
          <a href={`/api/patient/portal/${sid}/consent/signed-pdf/${env.id}`}
              className="btn-secondary text-sm">
            Download
          </a>
        )}
      </div>
    </li>
  )
}

function ResendCard({ sid, onResend, busy, err }) {
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
      <div className="text-sm text-amber-700 font-medium">
        Consent forms not ready yet
      </div>
      <p className="text-sm text-gray-700 mt-1">
        Your forms should have been sent automatically. If you don't see
        them, click below to send them now.
      </p>
      {err && <p className="text-sm text-red-600 mt-2">{err}</p>}
      <button onClick={onResend} disabled={busy}
               className="btn-primary mt-3">
        {busy ? 'Sending…' : 'Send consent forms'}
      </button>
    </div>
  )
}

export default function Consent() {
  const { sid } = useParams()
  const qc = useQueryClient()
  const [resendErr, setResendErr] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['portal-consent', sid],
    queryFn: () => portalApi.get(`/${sid}/consent`).then(r => r.data),
    // Poll every 5 seconds while any envelope is in flight, otherwise stop.
    refetchInterval: (q) => {
      const d = q.state.data
      if (!d) return false
      const anyInFlight = (d.envelopes || []).some(e =>
        ['sent', 'delivered', 'pending', 'in_progress'].includes(e.status),
      )
      return anyInFlight ? 5000 : false
    },
    staleTime: 5_000,
  })

  const resend = useMutation({
    mutationFn: () => portalApi.post(`/${sid}/consent/resend`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portal-consent', sid] }),
    onError: (e) => setResendErr(e?.response?.data?.detail || 'Could not send.'),
  })

  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-gray-900">Consent forms</h1>

      {/* Unscheduled and unsent → empty state explaining the flow */}
      {data.envelopes.length === 0 && !data.scheduled_date && (
        <EmptyState scheduledDate={null} sid={sid} />
      )}

      {/* Scheduled but no envelopes → auto-send must have failed; show resend */}
      {data.envelopes.length === 0 && data.scheduled_date && (
        <ResendCard sid={sid}
                       onResend={() => { setResendErr(''); resend.mutate() }}
                       busy={resend.isPending} err={resendErr} />
      )}

      {/* Envelopes exist → status list */}
      {data.envelopes.length > 0 && (
        <section className="bg-white rounded-lg shadow p-4">
          <ul className="divide-y divide-gray-100">
            {data.envelopes.map(env => (
              <EnvelopeRow key={env.id} env={env} sid={sid} />
            ))}
          </ul>
          {data.all_complete && (
            <div className="mt-3 text-sm text-green-700">
              ✓ All consent forms have been signed by all parties.
            </div>
          )}
        </section>
      )}
    </div>
  )
}
```

The `EnvelopeRow` download link uses a direct `<a href>` to the backend endpoint. Because the portal axios client sets `Authorization` from localStorage on every request, the bare `<a>` won't automatically send the Bearer token. Workaround: the backend route depends on `require_portal_token` reading the Authorization header, which a plain `<a>` link won't send.

**Fix:** instead of a direct `<a>`, use a click handler that fetches via axios and triggers a download:

Replace the `{env.can_download && ...}` block with:

```jsx
{env.can_download && <DownloadButton sid={sid} env={env} />}
```

And add `DownloadButton` at the top of the file:

```jsx
function DownloadButton({ sid, env }) {
  const [busy, setBusy] = useState(false)
  async function go() {
    setBusy(true)
    try {
      const r = await portalApi.get(
        `/${sid}/consent/signed-pdf/${env.id}`,
        { responseType: 'blob' },
      )
      const url = URL.createObjectURL(r.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${(env.template_name || 'consent').replace(/[^a-z0-9]/gi,'_')}.pdf`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } finally { setBusy(false) }
  }
  return (
    <button onClick={go} disabled={busy} className="btn-secondary text-sm">
      {busy ? 'Loading…' : 'Download'}
    </button>
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
git add frontend/src/pages/portal/Consent.jsx frontend/src/pages/portal/stubs/ConsentStub.jsx \
        frontend/src/App.jsx
git commit -m "feat(portal-p3): Consent page (status list + Sign now + Download + Resend)"
```

---

## Task 7: Drop "soon" from Consent nav

**Files:**
- Modify: `frontend/src/pages/portal/PortalShell.jsx`

- [ ] **Step 1: Update the NAV array.** Remove `comingSoon: true` from the `consent` entry:

```jsx
const NAV = [
  { to: '',          label: 'Dashboard' },
  { to: 'payments',  label: 'Payments' },
  { to: 'schedule',  label: 'Schedule' },
  { to: 'consent',   label: 'Consent' },
  { to: 'documents', label: 'Documents', comingSoon: true },
  { to: 'messages',  label: 'Messages',  comingSoon: true },
]
```

- [ ] **Step 2: Build check + commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/portal/PortalShell.jsx
git commit -m "feat(portal-p3): drop 'soon' from Consent nav item"
```

---

## Task 8: Smoke test in prod (manual)

Done after Tasks 1–7 are merged and deployed. I drive this.

- [ ] **Step 1: Push, build, deploy** backend `v43` + frontend `v_portal_p3`. No DB migration this time (no schema changes).

- [ ] **Step 2: Insert a test surgery** with my cell, `patient_responsibility = 0` (so the schedule gate is open immediately), eligible facility office, `procedure_classification = office_d_and_c`. Insert a block day 14 days out. Same SQL pattern as P2 T12.

- [ ] **Step 3: Portal sign-in** (DOB + last4 → SMS → code → dashboard). Confirm Consent nav no longer shows "· soon".

- [ ] **Step 4: Schedule a slot.** GET /portal/.../slots → POST /portal/.../slots/{bd}/claim → confirm 200. **Confirm a BoldSign envelope is created** via direct API check (or by hitting GET /consent and seeing a row appear).

- [ ] **Step 5: Hit GET /consent.** Should show one envelope row, status `sent`.

- [ ] **Step 6: Hit GET /consent/sign-link/{env_id}.** Should return a `signLink` URL pointing to `app.boldsign.com/document/sign/...`. Open it in browser, sign as the patient.

- [ ] **Step 7: Wait ~5–10 seconds for the BoldSign webhook to update the row.** Hit GET /consent again — patient signing should be reflected (envelope still `in_progress` until surgeon + witness sign, but webhook events will arrive).

- [ ] **Step 8: Optionally void the test envelope from BoldSign Dashboard** so the surgeon + witness don't get test emails.

- [ ] **Step 9: Cleanup** — delete the test surgery, payment rows (none expected since $0 balance), block day, auth attempts. Close Cloud SQL public IP.
