# Patient Portal P5b — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire patient-facing FMLA submission — upload blank form → pay $25 fee via SMS-step-up Stripe Checkout → see status → download completed form from office.

**Architecture:** No new tables. Three new columns on `Surgery`, one new column on `SurgeryPayment` (a `kind` discriminator so the existing webhook routes FMLA fees away from `Surgery.amount_paid`). Reuses P5's upload service, P2's step-up SMS, and Stripe Checkout. Five new portal endpoints + one new frontend card + an extracted shared `StepUpPayFlow` component.

**Spec:** `docs/superpowers/specs/2026-06-01-patient-portal-p5b-design.md`

**Deploy prerequisite:**
```bash
gcloud run services update backend --project=wwc-solutions --region=us-east4 \
  --update-env-vars=FMLA_FEE_CENTS=2500
```
(Defaults to 2500 if unset; setting it explicitly makes the value visible in Cloud Run config.)

**Key facts (don't relitigate):**
- `Surgery.fmla_status` (String(40), nullable) already exists at `surgery.py:262`.
- `Surgery.documents` (P5 T1) relationship is ordered by `uploaded_at DESC`. Filter by `kind=="fmla_blank"` or `kind=="fmla_completed"`.
- `app/services/surgery_uploads.py` exposes `store_upload(...)` and `signed_download_url(...)` with the IAM-delegated V4 signing fix (commit `f893db2`).
- `app/services/patient_portal_auth.py` exposes `issue_challenge(s, purpose="payment")` and `verify_code()`.
- `app/services/stripe_payments.py` has `create_checkout_session(db, s, *, amount, description, actor)` returning a `SurgeryPayment` row. The webhook `_handle_checkout_completed` is where status updates happen.

---

## Task 1: Schema — 3 Surgery columns + SurgeryPayment.kind + migration

**Files:**
- Modify: `backend/app/models/surgery.py` — 3 columns on Surgery
- Modify: `backend/app/models/stripe_payment.py` — `kind` column on SurgeryPayment
- Create: `backend/scripts/migrate_patient_portal_p5b.py`
- Test:   `backend/tests/test_patient_portal_p5b_schema.py`

- [ ] **Step 1: Failing test** at `backend/tests/test_patient_portal_p5b_schema.py`:

```python
"""Patient portal P5b schema — FMLA fee tracking."""
from decimal import Decimal

from app.models.surgery import Surgery
from app.models.stripe_payment import SurgeryPayment


def test_surgery_has_fmla_fee_columns(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    assert s.fmla_fee_paid is False
    assert s.fmla_fee_paid_at is None
    assert s.fmla_fee_stripe_session_id is None


def test_surgery_payment_has_kind(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    p = SurgeryPayment(
        surgery_id=s.id,
        status="paid",
        amount_requested=Decimal("25.00"),
        amount_paid=Decimal("25.00"),
        amount_refunded=Decimal("0.00"),
        currency="usd",
        requested_by="patient:portal",
    )
    db.add(p); db.commit(); db.refresh(p)
    # Default must be "patient_balance"
    assert p.kind == "patient_balance"


def test_surgery_payment_kind_can_be_fmla_fee(db):
    s = Surgery(chart_number="2", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    p = SurgeryPayment(
        surgery_id=s.id,
        status="requested",
        kind="fmla_fee",
        amount_requested=Decimal("25.00"),
        amount_paid=Decimal("0.00"),
        amount_refunded=Decimal("0.00"),
        currency="usd",
        requested_by="patient:portal",
    )
    db.add(p); db.commit(); db.refresh(p)
    assert p.kind == "fmla_fee"
```

- [ ] **Step 2: Run, confirm fail.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_patient_portal_p5b_schema.py -v
```

- [ ] **Step 3: Add columns to Surgery.** Find the existing `fmla_status` column at `surgery.py:262`. Add right after it:

```python
    # FMLA processing fee tracking (P5b — patient self-service flow)
    fmla_fee_paid           = Column(Boolean, default=False, nullable=False)
    fmla_fee_paid_at        = Column(DateTime, nullable=True)
    fmla_fee_stripe_session_id = Column(String(100), nullable=True)
```

- [ ] **Step 4: Add `kind` to SurgeryPayment.** Open `backend/app/models/stripe_payment.py`. Find the `SurgeryPayment` class. Add a `kind` column near the top of the column block:

```python
    kind = Column(String(40), default="patient_balance", nullable=False)
    # "patient_balance" — payment toward Surgery.patient_responsibility
    #                     (webhook bumps Surgery.amount_paid)
    # "fmla_fee"        — FMLA processing fee
    #                     (webhook sets fmla_fee_paid; doesn't touch amount_paid)
```

- [ ] **Step 5: Run, confirm pass.**

- [ ] **Step 6: Create the migration** at `backend/scripts/migrate_patient_portal_p5b.py`:

```python
"""Idempotent migration for Patient Portal P5b.

Adds:
  - 3 columns on `surgeries`: fmla_fee_paid(+_at, +_stripe_session)
  - 1 column on `surgery_payments`: kind (default 'patient_balance')

Run on prod:
    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/migrate_patient_portal_p5b.py
"""
import os
import sys
from sqlalchemy import create_engine, text

DDL = [
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS fmla_fee_paid BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS fmla_fee_paid_at TIMESTAMP NULL""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS fmla_fee_stripe_session_id VARCHAR(100) NULL""",
    """ALTER TABLE surgery_payments
       ADD COLUMN IF NOT EXISTS kind VARCHAR(40) NOT NULL DEFAULT 'patient_balance'""",
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr); sys.exit(2)
    eng = create_engine(db_url)
    with eng.begin() as conn:
        for ddl in DDL:
            conn.execute(text(ddl))
            print(f"  ✓ {ddl.split(chr(10))[0][:80]}")
    print("\nDone.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/models/surgery.py backend/app/models/stripe_payment.py \
        backend/scripts/migrate_patient_portal_p5b.py \
        backend/tests/test_patient_portal_p5b_schema.py
git commit -m "feat(portal-p5b): schema — Surgery fmla_fee columns + SurgeryPayment.kind"
```

---

## Task 2: Stripe extension — FMLA checkout + webhook routing

**Files:**
- Modify: `backend/app/services/stripe_payments.py` — accept `kind` kwarg in `create_checkout_session`, route in `_handle_checkout_completed`
- Modify: `backend/tests/test_stripe_payments_service.py` — append 3 tests

The existing `create_checkout_session` creates a `SurgeryPayment` with no explicit `kind` (defaults to "patient_balance" per T1). FMLA flows need to pass `kind="fmla_fee"`. The webhook handler in the same module routes the post-payment state update based on `kind`.

- [ ] **Step 1: Read the existing module** to understand the signatures.

```bash
/usr/bin/grep -n "def create_checkout_session\|def _handle_checkout_completed\|amount_paid =" \
  /Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/stripe_payments.py \
  /Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/stripe_payments.py
```

(The webhook handler is currently in `routers/stripe_payments.py` per the existing layout. The "service" file is `services/stripe_payments.py` for Checkout session creation.)

- [ ] **Step 2: Failing tests** at `backend/tests/test_stripe_payments_service.py`. Append:

```python
def test_create_checkout_session_marks_kind_fmla_fee(db):
    """When kind='fmla_fee' is passed, the SurgeryPayment row records it."""
    from decimal import Decimal
    from unittest.mock import patch, MagicMock
    from app.models.surgery import Surgery
    from app.models.stripe_payment import SurgeryPayment
    from app.services.stripe_payments import create_checkout_session

    s = Surgery(chart_number="1", patient_name="Pat", status="new",
                  email="p@example.com")
    db.add(s); db.commit(); db.refresh(s)

    fake_session = MagicMock()
    fake_session.id = "cs_test_123"
    fake_session.url = "https://stripe.test/cs_test_123"
    with patch("app.services.stripe_payments.is_configured",
                return_value=True), \
         patch("app.services.stripe_payments.stripe.checkout.Session.create",
                return_value=fake_session):
        pay = create_checkout_session(
            db, s,
            amount=Decimal("25.00"),
            description="FMLA processing fee",
            actor="patient:portal",
            kind="fmla_fee",
        )

    db.refresh(pay)
    assert pay.kind == "fmla_fee"


def test_create_checkout_session_defaults_kind_patient_balance(db):
    """Backward-compat: callers that don't pass kind get the default."""
    from decimal import Decimal
    from unittest.mock import patch, MagicMock
    from app.models.surgery import Surgery
    from app.services.stripe_payments import create_checkout_session

    s = Surgery(chart_number="2", patient_name="Pat", status="new",
                  email="p@example.com")
    db.add(s); db.commit(); db.refresh(s)

    fake_session = MagicMock()
    fake_session.id = "cs_test_456"
    fake_session.url = "https://stripe.test/cs_test_456"
    with patch("app.services.stripe_payments.is_configured",
                return_value=True), \
         patch("app.services.stripe_payments.stripe.checkout.Session.create",
                return_value=fake_session):
        pay = create_checkout_session(
            db, s,
            amount=Decimal("250.00"),
            description="Surgery balance",
            actor="patient:portal",
        )

    db.refresh(pay)
    assert pay.kind == "patient_balance"
```

If the test file doesn't exist yet, create it; otherwise append.

- [ ] **Step 3: Run, confirm fail** (TypeError on unexpected kwarg `kind` — or the kind attribute defaults to None):

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_stripe_payments_service.py -v -k "kind"
```

- [ ] **Step 4: Update the service.** In `backend/app/services/stripe_payments.py`, find `create_checkout_session`. Add `kind: str = "patient_balance"` to the signature and pass it when constructing the `SurgeryPayment` row:

```python
def create_checkout_session(
    db: Session,
    surgery: Surgery,
    *,
    amount: Decimal,
    description: str,
    actor: str,
    kind: str = "patient_balance",
) -> SurgeryPayment:
    ...
    pay = SurgeryPayment(
        surgery_id=surgery.id,
        status="requested",
        kind=kind,        # ← NEW
        amount_requested=amount,
        amount_paid=Decimal("0.00"),
        amount_refunded=Decimal("0.00"),
        currency="usd",
        requested_by=actor,
        # ... rest unchanged
    )
    ...
```

The exact location of the SurgeryPayment construction depends on the existing file structure — find the assignment to `pay = SurgeryPayment(...)` and add `kind=kind` to the kwargs.

- [ ] **Step 5: Update the webhook handler.** In `backend/app/routers/stripe_payments.py`, find `_handle_checkout_completed`. The current code bumps `s.amount_paid` unconditionally. Wrap that in a kind check and add the FMLA branch:

```python
def _handle_checkout_completed(db, event_type, obj):
    # ... existing setup that finds `pay` (SurgeryPayment) and `s` (Surgery) ...
    amount_paid = Decimal(obj.get("amount_total", 0)) / Decimal(100)
    pay.amount_paid = amount_paid
    pay.status = "paid"
    pay.paid_at = datetime.utcnow()
    session_id = obj.get("id")

    if pay.kind == "fmla_fee":
        s.fmla_fee_paid = True
        s.fmla_fee_paid_at = datetime.utcnow()
        s.fmla_fee_stripe_session_id = session_id
        # Auto-flip status if blank upload already exists
        has_blank = any(d.kind == "fmla_blank" for d in (s.documents or []))
        if has_blank and (s.fmla_status or "") in ("", None):
            s.fmla_status = "submitted"
    else:
        # Existing patient_balance behavior
        s.amount_paid = (s.amount_paid or 0) + amount_paid

    # ... rest of the handler (email receipt, etc.) ...
```

If the existing handler doesn't separate the SurgeryPayment update from the Surgery.amount_paid bump, refactor so the conditional is clean.

- [ ] **Step 6: Add a webhook routing test.** Append to `backend/tests/test_stripe_endpoints.py` (or whichever file tests the webhook):

```python
def test_webhook_fmla_fee_sets_fmla_fee_paid_and_doesnt_bump_amount_paid(client, db):
    """A paid SurgeryPayment with kind='fmla_fee' must:
      - set Surgery.fmla_fee_paid = True
      - NOT bump Surgery.amount_paid
      - auto-flip fmla_status to 'submitted' if blank upload exists
    """
    from decimal import Decimal
    from unittest.mock import patch
    from datetime import datetime
    from app.models.surgery import Surgery, SurgeryDocument
    from app.models.stripe_payment import SurgeryPayment

    s = Surgery(chart_number="X", patient_name="Pat", status="new",
                  patient_responsibility=Decimal("500.00"),
                  amount_paid=Decimal("100.00"))
    db.add(s); db.commit(); db.refresh(s)
    # Pre-existing blank upload
    db.add(SurgeryDocument(
        surgery_id=s.id, kind="fmla_blank",
        filename="my_fmla.pdf",
        gcs_path=f"surgery-uploads/{s.id}/fmla_blank/x.pdf",
        uploaded_by="patient:portal",
    ))
    # The pending FMLA payment row
    pay = SurgeryPayment(
        surgery_id=s.id, status="requested", kind="fmla_fee",
        amount_requested=Decimal("25.00"),
        amount_paid=Decimal("0.00"), amount_refunded=Decimal("0.00"),
        currency="usd", requested_by="patient:portal",
        stripe_session_id="cs_test_fmla",
    )
    db.add(pay); db.commit(); db.refresh(pay)

    # Simulate the Stripe checkout.session.completed event
    from app.routers.stripe_payments import _handle_checkout_completed
    _handle_checkout_completed(db, "checkout.session.completed", {
        "id": "cs_test_fmla",
        "amount_total": 2500,   # $25.00 in cents
    })
    db.refresh(s); db.refresh(pay)
    assert pay.status == "paid"
    assert s.fmla_fee_paid is True
    assert s.fmla_fee_paid_at is not None
    assert s.fmla_status == "submitted"
    # amount_paid unchanged — FMLA fee doesn't touch it
    assert s.amount_paid == Decimal("100.00")
```

The exact import for `_handle_checkout_completed` depends on whether it's an underscore-private function. If it's accessible at module scope, import it directly; otherwise use the public webhook endpoint via the test client.

- [ ] **Step 7: Run, confirm pass.**

- [ ] **Step 8: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/stripe_payments.py backend/app/routers/stripe_payments.py \
        backend/tests/test_stripe_payments_service.py backend/tests/test_stripe_endpoints.py
git commit -m "feat(portal-p5b): create_checkout_session(kind=) + webhook routes FMLA fees"
```

---

## Task 3: POST /fmla/upload

**Files:**
- Modify: `backend/app/routers/patient_portal.py` — append handler
- Modify: `backend/tests/test_patient_portal_endpoints.py` — append 3 tests

- [ ] **Step 1: Failing tests** — append:

```python
def test_fmla_upload_creates_fmla_blank_document(client, db):
    from unittest.mock import patch, MagicMock
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    pdf_bytes = b"%PDF-1.4\nfmla blank form\n"
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = blob
        r = client.post(
            f"/api/patient/portal/{s.id}/fmla/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("my_fmla.pdf", pdf_bytes, "application/pdf")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "fmla_blank"
    assert body["filename"] == "my_fmla.pdf"


def test_fmla_upload_flips_status_when_fee_already_paid(client, db):
    """If patient paid the fee BEFORE uploading (unusual but possible),
    upload auto-flips fmla_status to 'submitted'."""
    from unittest.mock import patch, MagicMock
    from datetime import datetime
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    s.fmla_fee_paid = True
    s.fmla_fee_paid_at = datetime.utcnow()
    db.commit()
    token = issue_portal_token(s)
    pdf_bytes = b"%PDF-1.4\nfmla\n"
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = blob
        r = client.post(
            f"/api/patient/portal/{s.id}/fmla/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("fmla.pdf", pdf_bytes, "application/pdf")},
        )
    assert r.status_code == 200
    db.refresh(s)
    assert s.fmla_status == "submitted"


def test_fmla_upload_does_not_flip_status_when_fee_unpaid(client, db):
    """Upload alone (no fee paid) leaves status unchanged."""
    from unittest.mock import patch, MagicMock
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    pdf_bytes = b"%PDF-1.4\nfmla\n"
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = blob
        r = client.post(
            f"/api/patient/portal/{s.id}/fmla/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("fmla.pdf", pdf_bytes, "application/pdf")},
        )
    assert r.status_code == 200
    db.refresh(s)
    # fmla_status was NULL before; should still be NULL
    assert (s.fmla_status or "") == ""
```

- [ ] **Step 2: Run, confirm fail** (404 on endpoint).

- [ ] **Step 3: Add handler** to `backend/app/routers/patient_portal.py`. Append:

```python
@router.post("/{surgery_id}/fmla/upload")
async def portal_fmla_upload(
    surgery_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Patient uploads their employer-provided blank FMLA form."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    contents = await file.read()
    from app.services.surgery_uploads import store_upload, UploadError
    try:
        doc = store_upload(
            db, s, kind="fmla_blank",
            filename=file.filename or "fmla.pdf",
            file_bytes=contents,
            content_type=file.content_type or "application/octet-stream",
            uploaded_by="patient:portal",
        )
    except UploadError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    # If the fee was already paid (unusual flow), this completes the
    # submission and flips status to "submitted".
    if s.fmla_fee_paid and (s.fmla_status or "") in ("", None):
        s.fmla_status = "submitted"
        db.commit()
    return {
        "id":           str(doc.id),
        "kind":         doc.kind,
        "filename":     doc.filename,
        "uploaded_at":  doc.uploaded_at.isoformat(),
        "fmla_status":  s.fmla_status or "",
    }
```

`File` and `UploadFile` should already be imported (P5 T5 added them). Verify before assuming.

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p5b): POST /fmla/upload — kind=fmla_blank + auto-flip status"
```

---

## Task 4: POST /fmla/step-up + /fmla/checkout

**Files:**
- Modify: `backend/app/routers/patient_portal.py`
- Modify: `backend/tests/test_patient_portal_endpoints.py`

These mirror P2's `/payments/step-up` + `/payments/checkout` flow exactly. The only differences: amount comes from `FMLA_FEE_CENTS` env var (default 2500), `kind="fmla_fee"` is passed to the Stripe service, and the gating skips the "outstanding balance" check (FMLA fee is always due if requested).

- [ ] **Step 1: Failing tests** — append:

```python
def test_fmla_step_up_sends_payment_purpose_sms(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.patient_portal_auth.send_sms",
                return_value=True) as mock_sms:
        r = client.post(f"/api/patient/portal/{s.id}/fmla/step-up",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert "step_up_token" in r.json()
    body = mock_sms.call_args[0][1]
    assert "payment" in body.lower() or "charge" in body.lower()


def test_fmla_checkout_rejects_invalid_code(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"), \
         patch("app.services.patient_portal_auth.send_sms",
                return_value=True):
        step = client.post(
            f"/api/patient/portal/{s.id}/fmla/step-up",
            headers={"Authorization": f"Bearer {token}"}
        ).json()
    r = client.post(
        f"/api/patient/portal/{s.id}/fmla/checkout",
        json={"step_up_token": step["step_up_token"], "code": "000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_fmla_checkout_creates_session_with_kind_fmla_fee(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)

    class FakePay:
        id = "pay_fmla_test"
        checkout_url = "https://stripe.test/cs_fmla"

    captured = {}
    def _capture(db, surgery, *, amount, description, actor, kind=None):
        captured["amount"] = amount
        captured["kind"]   = kind
        captured["actor"]  = actor
        return FakePay()

    with patch("app.services.patient_portal_auth._generate_code",
                return_value="111111"), \
         patch("app.services.patient_portal_auth.send_sms",
                return_value=True), \
         patch("app.services.stripe_payments.is_configured",
                return_value=True), \
         patch("app.services.stripe_payments.create_checkout_session",
                side_effect=_capture):
        step = client.post(
            f"/api/patient/portal/{s.id}/fmla/step-up",
            headers={"Authorization": f"Bearer {token}"}
        ).json()
        r = client.post(
            f"/api/patient/portal/{s.id}/fmla/checkout",
            json={"step_up_token": step["step_up_token"], "code": "111111"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"].startswith("https://stripe.test/")
    # Critical: the service got kind="fmla_fee"
    assert captured["kind"] == "fmla_fee"
    # Default fee = $25 = Decimal("25.00")
    from decimal import Decimal
    assert captured["amount"] == Decimal("25.00")
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add handlers** to `backend/app/routers/patient_portal.py`. Append at the END:

```python
# ─── /{surgery_id}/fmla/step-up + /fmla/checkout ──────────────────

@router.post("/{surgery_id}/fmla/step-up")
def portal_fmla_step_up(
    surgery_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Send a fresh SMS code for FMLA fee payment authorization."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.fmla_fee_paid:
        raise HTTPException(status_code=422,
                              detail="The FMLA fee has already been paid.")
    if not (s.cell_phone or s.phone or "").strip():
        raise HTTPException(status_code=409,
                              detail="No phone on file — call our office at "
                                     "240-252-2140.")
    challenge_token, _code = auth.issue_challenge(db, s, purpose="payment")
    return {"step_up_token": challenge_token}


class FmlaCheckoutPayload(BaseModel):
    step_up_token: str
    code: str


@router.post("/{surgery_id}/fmla/checkout")
def portal_fmla_checkout(
    surgery_id: str,
    payload: FmlaCheckoutPayload,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    """Verify SMS code, create Stripe Checkout for the FMLA processing
    fee. Returns the URL the browser should visit."""
    code = "".join(c for c in (payload.code or "") if c.isdigit())
    if len(code) != 6:
        raise HTTPException(status_code=401, detail="Invalid code")
    matched_sid = auth.verify_code(db, payload.step_up_token, code)
    if matched_sid is None or matched_sid != surgery_id:
        raise HTTPException(status_code=401, detail="Invalid code")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    if s.fmla_fee_paid:
        raise HTTPException(status_code=422,
                              detail="The FMLA fee has already been paid.")
    import os
    from decimal import Decimal
    fee_cents = int(os.environ.get("FMLA_FEE_CENTS", "2500") or "2500")
    fee = Decimal(fee_cents) / Decimal(100)
    if not stripe_svc.is_configured():
        raise HTTPException(status_code=503,
                              detail="Payments aren't available right now.")
    try:
        pay = stripe_svc.create_checkout_session(
            db, s,
            amount=fee,
            description="FMLA processing fee",
            actor="patient:portal:fmla",
            kind="fmla_fee",
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("portal FMLA checkout failed")
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")
    return {"checkout_url": pay.checkout_url, "payment_id": str(pay.id)}
```

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p5b): POST /fmla/step-up + /fmla/checkout (Stripe with kind=fmla_fee)"
```

---

## Task 5: GET /fmla

**Files:**
- Modify: `backend/app/routers/patient_portal.py`
- Modify: `backend/tests/test_patient_portal_endpoints.py`

Returns the aggregated FMLA state: status, fee amount, fee_paid flag, blank uploads, and any completed uploads (with signed download URLs).

- [ ] **Step 1: Failing tests** — append:

```python
def test_fmla_get_when_not_started(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/fmla",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == ""    # not started
    assert body["fee_paid"] is False
    assert body["fee_amount"] == "25.00"  # default
    assert body["blank_uploads"] == []
    assert body["completed_uploads"] == []


def test_fmla_get_returns_uploads_with_signed_urls(client, db):
    from unittest.mock import patch
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery import SurgeryDocument
    s = _seed_surgery(db)
    s.fmla_status = "submitted"
    s.fmla_fee_paid = True
    db.add(SurgeryDocument(
        surgery_id=s.id, kind="fmla_blank",
        filename="my_form.pdf",
        gcs_path=f"surgery-uploads/{s.id}/fmla_blank/x.pdf",
        uploaded_by="patient:portal",
    ))
    db.add(SurgeryDocument(
        surgery_id=s.id, kind="fmla_completed",
        filename="filled.pdf",
        gcs_path=f"surgery-uploads/{s.id}/fmla_completed/y.pdf",
        uploaded_by="staff:coordinator",
    ))
    db.commit()
    token = issue_portal_token(s)
    with patch("app.services.surgery_uploads.signed_download_url",
                return_value="https://signed.example/x"):
        r = client.get(f"/api/patient/portal/{s.id}/fmla",
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "submitted"
    assert body["fee_paid"] is True
    assert len(body["blank_uploads"]) == 1
    assert len(body["completed_uploads"]) == 1
    assert body["completed_uploads"][0]["download_url"].startswith("https://signed.example/")
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Add handler** to `backend/app/routers/patient_portal.py`. Append:

```python
@router.get("/{surgery_id}/fmla")
def portal_fmla(surgery_id: str, db: Session = Depends(get_db),
                  _: str = Depends(require_portal_token)):
    """Return aggregated FMLA state for the patient's UI."""
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    import os
    from decimal import Decimal
    from app.services.surgery_uploads import signed_download_url
    fee_cents = int(os.environ.get("FMLA_FEE_CENTS", "2500") or "2500")
    fee = Decimal(fee_cents) / Decimal(100)

    def _doc_summary(d, include_url: bool) -> dict:
        url = None
        if include_url:
            try:
                url = signed_download_url(d, ttl_minutes=5)
            except Exception:
                url = None
        return {
            "id":          str(d.id),
            "filename":    d.filename,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            "download_url": url,
        }

    blank_uploads = [
        _doc_summary(d, include_url=False)
        for d in (s.documents or []) if d.kind == "fmla_blank"
    ]
    completed_uploads = [
        _doc_summary(d, include_url=True)
        for d in (s.documents or []) if d.kind == "fmla_completed"
    ]

    return {
        "status":         s.fmla_status or "",
        "fee_amount":     f"{fee:.2f}",
        "fee_paid":       bool(s.fmla_fee_paid),
        "fee_paid_at":    s.fmla_fee_paid_at.isoformat() if s.fmla_fee_paid_at else None,
        "blank_uploads":  blank_uploads,
        "completed_uploads": completed_uploads,
    }
```

The blank uploads don't include signed URLs by default (patient already has the file; no need to download again). Completed uploads include signed URLs since that's the patient's primary mechanism for retrieving the office-filled form.

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/patient_portal.py backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p5b): GET /fmla — aggregated status + uploads + signed URLs"
```

---

## Task 6: Extract `StepUpPayFlow` shared component

**Files:**
- Read first: `frontend/src/pages/portal/Payments.jsx` (where the existing `PayFlow` lives)
- Create: `frontend/src/components/portal/StepUpPayFlow.jsx`
- Modify: `frontend/src/pages/portal/Payments.jsx` — replace inline PayFlow with the shared component

P2's `PayFlow` is a self-contained state machine: send code → enter code → submit → redirect to Stripe. P5b needs the same flow with different endpoints. Extract once, reuse.

- [ ] **Step 1: Read the existing `PayFlow`** to understand its current shape:

```bash
/usr/bin/grep -n "function PayFlow\|stage ===" \
  /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/portal/Payments.jsx | /usr/bin/head -20
```

- [ ] **Step 2: Create the shared component** at `frontend/src/components/portal/StepUpPayFlow.jsx`. It accepts the step-up URL and checkout URL as props, plus an `onCancel` callback:

```jsx
import { useState, useEffect, useRef } from 'react'
import { portalApi } from '../../lib/portal-api'

/**
 * SMS-step-up + 6-digit code entry + Stripe Checkout redirect.
 * Used by both surgery balance payment (P2) and FMLA fee payment (P5b).
 *
 * Props:
 *   stepUpUrl   — e.g. `/${sid}/payments/step-up` or `/${sid}/fmla/step-up`
 *   checkoutUrl — e.g. `/${sid}/payments/checkout` or `/${sid}/fmla/checkout`
 *   onCancel    — called when the user backs out
 */
export default function StepUpPayFlow({ stepUpUrl, checkoutUrl, onCancel }) {
  const [stage, setStage] = useState('sending')   // sending | code | redirecting | error
  const [token, setToken] = useState(null)
  const [digits, setDigits] = useState(['','','','','',''])
  const [err, setErr] = useState('')
  const refs = useRef([])

  useEffect(() => {
    let cancelled = false
    portalApi.post(stepUpUrl).then(r => {
      if (cancelled) return
      setToken(r.data.step_up_token)
      setStage('code')
    }).catch(e => {
      if (cancelled) return
      setErr(e?.response?.data?.detail || 'Could not start payment.')
      setStage('error')
    })
    return () => { cancelled = true }
  }, [stepUpUrl])

  function setDigit(i, v) {
    const c = v.replace(/\D/g, '').slice(-1)
    const next = [...digits]; next[i] = c; setDigits(next)
    if (c && i < 5) refs.current[i+1]?.focus()
  }

  async function submit(e) {
    e?.preventDefault?.()
    const code = digits.join('')
    if (code.length !== 6) return
    setErr(''); setStage('redirecting')
    try {
      const { data } = await portalApi.post(checkoutUrl, {
        step_up_token: token, code,
      })
      window.location.assign(data.checkout_url)
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Invalid code.')
      setStage('code')
    }
  }

  useEffect(() => {
    if (stage === 'code' && digits.every(d => d !== '')) submit()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digits, stage])

  if (stage === 'sending') {
    return <div className="text-sm text-gray-500 mt-4">Sending you a code…</div>
  }
  if (stage === 'redirecting') {
    return <div className="text-sm text-gray-500 mt-4">Redirecting to Stripe…</div>
  }
  if (stage === 'error') {
    return (
      <div className="mt-4">
        <div className="text-sm text-red-600">{err}</div>
        <button onClick={onCancel} className="btn-secondary mt-2">Back</button>
      </div>
    )
  }
  return (
    <form onSubmit={submit} className="mt-4 space-y-3">
      <div className="text-sm text-gray-600">
        Enter the 6-digit code we just texted you. (5 min expiry.)
      </div>
      <div className="flex gap-2">
        {digits.map((d, i) => (
          <input key={i}
                  ref={el => refs.current[i] = el}
                  type="text" inputMode="numeric"
                  maxLength={1} value={d}
                  onChange={e => setDigit(i, e.target.value)}
                  className="w-10 h-12 text-center text-lg rounded border-gray-300" />
        ))}
      </div>
      {err && <div className="text-sm text-red-600">{err}</div>}
      <div className="flex gap-2">
        <button type="submit" disabled={digits.join('').length !== 6}
                 className="btn-primary">Continue</button>
        <button type="button" onClick={onCancel}
                 className="btn-secondary">Cancel</button>
      </div>
    </form>
  )
}
```

- [ ] **Step 3: Update `Payments.jsx`** to use the extracted component. Find the existing `PayFlow` function and the place where it's called. Replace the call with:

```jsx
import StepUpPayFlow from '../../components/portal/StepUpPayFlow'

// ... where PayFlow was rendered:
<StepUpPayFlow
  stepUpUrl={`/${sid}/payments/step-up`}
  checkoutUrl={`/${sid}/payments/checkout`}
  onCancel={() => setShowFlow(false)} />
```

Delete the original inline `PayFlow` function in Payments.jsx.

- [ ] **Step 4: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
```

- [ ] **Step 5: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/components/portal/StepUpPayFlow.jsx \
        frontend/src/pages/portal/Payments.jsx
git commit -m "refactor(portal): extract StepUpPayFlow shared component"
```

---

## Task 7: Frontend — FmlaCard on Documents page

**Files:**
- Modify: `frontend/src/pages/portal/Documents.jsx` — add FmlaCard component + state

`FmlaCard` is the most state-aware card so far. It has six possible UI states:
1. Not started (no upload, no payment)
2. Upload done, payment pending
3. Payment done, upload pending (corner case)
4. Both done — status="submitted"
5. status="in_review"
6. status="completed" with download

- [ ] **Step 1: Add the FmlaCard component** to `frontend/src/pages/portal/Documents.jsx`. After `ClearanceCard` and before the default export:

```jsx
import StepUpPayFlow from '../../components/portal/StepUpPayFlow'

function FmlaCard({ sid, fmla, refetchFmla }) {
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [showPay, setShowPay] = useState(false)

  async function upload() {
    if (!file) return
    setBusy(true); setErr('')
    try {
      const form = new FormData()
      form.append('file', file)
      await portalApi.post(`/${sid}/fmla/upload`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setFile(null)
      refetchFmla()
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Upload failed.')
    } finally { setBusy(false) }
  }

  if (!fmla) return null
  const hasBlank = (fmla.blank_uploads || []).length > 0
  const feePaid  = !!fmla.fee_paid
  const status   = fmla.status || ''

  // Status badge color
  const badge =
    status === 'completed'   ? 'bg-green-100 text-green-700' :
    status === 'in_review'   ? 'bg-amber-100 text-amber-700' :
    status === 'submitted'   ? 'bg-amber-100 text-amber-700' :
                                 'bg-gray-200 text-gray-700'

  return (
    <section className="bg-white rounded-lg shadow p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">FMLA paperwork</h2>
        <span className={`text-xs px-2 py-1 rounded ${badge}`}>
          {status || 'not started'}
        </span>
      </div>

      {/* COMPLETED → patient downloads */}
      {status === 'completed' && fmla.completed_uploads?.length > 0 && (
        <div>
          <p className="text-sm text-gray-700">
            Your completed FMLA paperwork is ready.
          </p>
          <ul className="text-sm mt-2">
            {fmla.completed_uploads.map(u => (
              <li key={u.id} className="flex items-center justify-between py-1">
                <span>{u.filename}</span>
                {u.download_url && (
                  <a href={u.download_url} target="_blank" rel="noreferrer"
                      className="btn-primary text-xs">Download</a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* IN_REVIEW → just a message */}
      {status === 'in_review' && (
        <p className="text-sm text-gray-700">
          We're filling out your FMLA paperwork. We'll let you know when it's ready.
        </p>
      )}

      {/* SUBMITTED → just a message */}
      {status === 'submitted' && (
        <p className="text-sm text-gray-700">
          ✓ Submitted. We're filling out your form and will have it ready
          within 5 business days.
        </p>
      )}

      {/* NOT STARTED — give the upload + pay UI */}
      {!status && (
        <>
          <p className="text-sm text-gray-600">
            If you need FMLA documentation for work, upload your employer's
            blank form and pay the ${fmla.fee_amount} processing fee.
          </p>

          {/* Upload step */}
          {!hasBlank && (
            <div>
              <div className="text-xs text-gray-500 mb-1">
                Step 1: Upload your employer's blank FMLA form
              </div>
              <div className="flex items-center gap-2">
                <input type="file"
                        accept="application/pdf,image/jpeg,image/png,image/heic"
                        onChange={e => setFile(e.target.files?.[0] || null)}
                        className="text-xs" />
                <button onClick={upload} disabled={!file || busy}
                         className="btn-primary text-sm">
                  {busy ? 'Uploading…' : 'Upload'}
                </button>
              </div>
              {err && <div className="text-xs text-red-600 mt-1">{err}</div>}
            </div>
          )}

          {hasBlank && (
            <div className="text-xs text-gray-600">
              ✓ Form received: {fmla.blank_uploads[0].filename}
            </div>
          )}

          {/* Pay step */}
          {!feePaid && hasBlank && !showPay && (
            <div>
              <div className="text-xs text-gray-500 mb-1">
                Step 2: Pay the ${fmla.fee_amount} processing fee
              </div>
              <button onClick={() => setShowPay(true)}
                       className="btn-primary text-sm">
                Pay ${fmla.fee_amount}
              </button>
            </div>
          )}

          {!feePaid && hasBlank && showPay && (
            <StepUpPayFlow
              stepUpUrl={`/${sid}/fmla/step-up`}
              checkoutUrl={`/${sid}/fmla/checkout`}
              onCancel={() => setShowPay(false)} />
          )}

          {/* Corner case: paid but no upload */}
          {feePaid && !hasBlank && (
            <div className="text-sm text-amber-700">
              Payment received — please upload your form to complete your request.
            </div>
          )}
        </>
      )}
    </section>
  )
}
```

- [ ] **Step 2: Wire the FMLA query** in the main `Documents` component:

```jsx
const { data: fmlaData, refetch: refetchFmla } = useQuery({
  queryKey: ['portal-fmla', sid],
  queryFn: () => portalApi.get(`/${sid}/fmla`).then(r => r.data),
  staleTime: 30_000,
})
```

And render `<FmlaCard />` at the bottom of the JSX (after `ClearanceCard`):

```jsx
<FmlaCard sid={sid} fmla={fmlaData} refetchFmla={refetchFmla} />
```

- [ ] **Step 3: Build check.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -8
```

- [ ] **Step 4: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/portal/Documents.jsx
git commit -m "feat(portal-p5b): FmlaCard — upload, pay, status, download"
```

---

## Task 8: Smoke test in prod (manual)

Done after Tasks 1–7 are merged and deployed. I drive this.

- [ ] **Step 1: Set the FMLA fee env var** so it's visible in Cloud Run config (even though the default 2500 works without it):

```bash
gcloud run services update backend --project=wwc-solutions --region=us-east4 \
  --update-env-vars=FMLA_FEE_CENTS=2500
```

- [ ] **Step 2: Push, build, deploy** backend `v46` + frontend `v_portal_p5b`. Run migration:

```bash
DATABASE_URL='postgresql+psycopg2://...' \
  ./venv/bin/python scripts/migrate_patient_portal_p5b.py
```

- [ ] **Step 3: Insert test surgery** with `version_id=1`, `procedure_classification="office_d_and_c"`, `cell_phone="+12405653594"`, `email="ocooke@waldorfwomenscare.com"`. No FMLA state set.

- [ ] **Step 4: Portal sign-in.** Navigate to Documents page.

- [ ] **Step 5: FMLA card visible, "not started" state.** Confirm copy mentions $25.

- [ ] **Step 6: Upload a fake PDF.** Card flips to "form received".

- [ ] **Step 7: Click "Pay $25"** → SMS step-up → enter code → Stripe Checkout URL appears in browser. Don't actually pay (we'd burn $25 on a test charge). Confirm the URL is live and matches `cs_live_*`.

- [ ] **Step 8: Simulate the Stripe webhook** by directly setting `fmla_fee_paid=True` in the DB. Refetch the FMLA card — should now show "Submitted" status.

  ```sql
  UPDATE surgeries SET fmla_fee_paid=true, fmla_fee_paid_at=NOW(),
    fmla_status='submitted' WHERE id='<test-sid>';
  ```

- [ ] **Step 9: Simulate the coordinator completing the form** by inserting a `fmla_completed` SurgeryDocument and bumping status:

  ```sql
  INSERT INTO surgery_documents (id, surgery_id, kind, filename, gcs_path,
    content_type, uploaded_at, uploaded_by)
  VALUES (gen_random_uuid()::char(36), '<test-sid>', 'fmla_completed',
    'completed_fmla.pdf', 'surgery-uploads/<test-sid>/fmla_completed/test.pdf',
    'application/pdf', NOW(), 'staff:smoke-test');
  UPDATE surgeries SET fmla_status='completed' WHERE id='<test-sid>';
  ```
  Upload a real PDF byte stream to the GCS object so the signed URL returns content.

- [ ] **Step 10: Refetch the FMLA card.** Confirm it shows "Completed" and a Download button. Click Download → file streams.

- [ ] **Step 11: Cleanup** — delete test surgery (cascade drops docs + payments), delete GCS objects, revoke the Stripe Checkout session via API.
