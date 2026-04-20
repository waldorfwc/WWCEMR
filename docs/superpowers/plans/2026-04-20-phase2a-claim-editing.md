# Phase 2a — Claim & Service-Line Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship side-drawer editors on `ClaimDetail.jsx` for claims, service lines, and their adjustments, backed by four routers (1 expanded + 3 new) that auto-compute `claim.balance` and audit every mutation.

**Architecture:** Granular REST (per-resource endpoints). Frontend drawers orchestrate sequential saves (claim PATCH → adj POST → adj PATCH → adj DELETE). Balance is always computed `billed − contractual − other − paid − pt_resp`; all other money fields are freeform. Auth is the existing `BILLING = admin + billing` guard applied at `include_router`.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend); React 18 + Vite + Tailwind + React Query v5 (frontend).

**Reference spec:** `docs/superpowers/specs/2026-04-20-phase2a-claim-editing-design.md`

---

## Pre-flight notes

- Fixtures already in `backend/tests/conftest.py`: `db`, `client` (admin), `clinical_client`, `billing_client`. No fixture changes needed.
- Claims router is already mounted in `main.py` with `dependencies=BILLING`. New routers will be added with the same guard.
- Existing pattern for `PATCH /claims/{id}` uses a `data: dict` body (no Pydantic). New endpoints follow the same pattern for consistency with the existing router — simpler than introducing Pydantic models when every field is independently optional.
- Audit service signature: `log_action(db, action, resource_type, resource_id=..., user_name=..., old_values=dict, new_values=dict, description=...)`. Commits internally; caller should commit its own changes **before** calling `log_action` (the service does its own commit+refresh).
- Frontend utility classes already exist in `frontend/src/index.css`: `card`, `input`, `btn-primary`, `btn-secondary`, `table-td`, `table-th`, `table-row`, `bg-plum-50`, `text-danger`, `text-success`, `text-muted`, `text-ink`. Follow the `Admin.jsx` styling precedent.
- React Query v5 invalidation pattern: `queryClient.invalidateQueries({ queryKey: ['claim', claimId] })` after full save success.
- `Decimal` vs float: SQLAlchemy `Numeric` columns round-trip as `Decimal`. The balance utility uses `(field or 0)` which coerces to int if `None`; Python mixes `int - Decimal` fine. Compare in tests using `float(row.balance)`.
- Money field inputs on the frontend send strings (from `<input type="number">` values); FastAPI will coerce Python `dict` JSON numbers to `Decimal` automatically on column assignment.

---

## Task 1: Backend — `claim_math.recompute_balance` utility + unit tests

**Files:**
- Create: `backend/app/services/claim_math.py`
- Create: `backend/tests/test_claim_math.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_claim_math.py`:

```python
"""Unit tests for the balance recompute utility."""
from decimal import Decimal
from app.models.claim import Claim
from app.services.claim_math import recompute_balance


def _make_claim(**kw) -> Claim:
    defaults = dict(
        billed_amount=Decimal("0"),
        allowed_amount=Decimal("0"),
        paid_amount=Decimal("0"),
        patient_responsibility=Decimal("0"),
        contractual_adjustment=Decimal("0"),
        other_adjustment=Decimal("0"),
        balance=Decimal("0"),
    )
    defaults.update(kw)
    return Claim(**defaults)


def test_recompute_balance_basic():
    c = _make_claim(
        billed_amount=Decimal("100"),
        contractual_adjustment=Decimal("10"),
        paid_amount=Decimal("80"),
        patient_responsibility=Decimal("5"),
    )
    recompute_balance(c)
    assert float(c.balance) == 5.0


def test_recompute_balance_zeros():
    c = _make_claim()
    recompute_balance(c)
    assert float(c.balance) == 0.0


def test_recompute_balance_negative_adjustment_increases_balance():
    c = _make_claim(
        billed_amount=Decimal("100"),
        contractual_adjustment=Decimal("-20"),
    )
    recompute_balance(c)
    assert float(c.balance) == 120.0


def test_recompute_balance_handles_none_fields():
    c = Claim(billed_amount=Decimal("50"))  # other money fields left None
    recompute_balance(c)
    assert float(c.balance) == 50.0


def test_recompute_balance_is_idempotent():
    c = _make_claim(billed_amount=Decimal("100"), paid_amount=Decimal("25"))
    recompute_balance(c)
    first = float(c.balance)
    recompute_balance(c)
    assert float(c.balance) == first == 75.0


def test_recompute_balance_does_not_touch_other_fields():
    c = _make_claim(billed_amount=Decimal("100"), notes="keep me")
    c.status = "pending"
    recompute_balance(c)
    assert c.notes == "keep me"
    assert c.status == "pending"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claim_math.py -v 2>&1 | tail -15
```
Expected: all 6 tests FAIL with `ModuleNotFoundError: No module named 'app.services.claim_math'`.

- [ ] **Step 3: Create `backend/app/services/claim_math.py`**

```python
"""Computed-field helpers for the Claim model."""
from decimal import Decimal
from app.models.claim import Claim


def recompute_balance(claim: Claim) -> None:
    """Set claim.balance = billed - contractual - other - paid - pt_resp.

    Mutates `claim` in place. Does NOT commit — caller commits the session.
    Handles None fields by treating them as zero.
    """
    claim.balance = (
        (claim.billed_amount or Decimal(0))
        - (claim.contractual_adjustment or Decimal(0))
        - (claim.other_adjustment or Decimal(0))
        - (claim.paid_amount or Decimal(0))
        - (claim.patient_responsibility or Decimal(0))
    )
```

- [ ] **Step 4: Run to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claim_math.py -v 2>&1 | tail -15
```
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/claim_math.py backend/tests/test_claim_math.py
git commit -m "feat(backend): claim_math.recompute_balance utility + unit tests"
```

---

## Task 2: Backend — expand `PATCH /api/claims/{claim_id}` allow-list + recompute balance

**Files:**
- Modify: `backend/app/routers/claims.py` (expand PATCH)
- Create: `backend/tests/test_claim_edit.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_claim_edit.py`:

```python
"""Tests for the expanded PATCH /api/claims/{claim_id} endpoint."""
from decimal import Decimal
from app.models.claim import Claim, ClaimStatus, InsuranceOrder
from app.models.patient import Patient
from app.models.audit import AuditLog


def _seed_claim(db, **overrides) -> Claim:
    c = Claim(
        claim_number="C0001",
        status=ClaimStatus.PENDING,
        billed_amount=Decimal("100"),
        contractual_adjustment=Decimal("10"),
        paid_amount=Decimal("80"),
        patient_responsibility=Decimal("5"),
        balance=Decimal("5"),
    )
    for k, v in overrides.items():
        setattr(c, k, v)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_patch_money_fields_recomputes_balance(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={"billed_amount": 200})
    assert r.status_code == 200, r.text
    body = r.json()
    # 200 - 10 - 0 - 80 - 5 = 105
    assert body["balance"] == 105.0


def test_patch_balance_in_body_is_ignored(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={"balance": 999})
    assert r.status_code == 200
    # Unchanged money fields → balance still 100-10-0-80-5 = 5
    assert r.json()["balance"] == 5.0


def test_patch_accepts_routing_fields(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={
        "payer_name": "Aetna", "subscriber_id": "SUB123",
        "group_number": "G1", "insurance_order": "secondary",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["payer_name"] == "Aetna"
    assert body["subscriber_id"] == "SUB123"
    assert body["group_number"] == "G1"
    assert body["insurance_order"] == "secondary"


def test_patch_accepts_date_fields(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={
        "date_of_service_from": "2026-01-15",
        "date_of_service_to": "2026-01-15",
        "check_date": "2026-02-01",
        "check_number": "CHK42",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["date_of_service_from"] == "2026-01-15"
    assert body["check_number"] == "CHK42"


def test_patch_accepts_identifiers_and_provider(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={
        "claim_number": "C9999",
        "payer_claim_number": "PCN-1",
        "rendering_provider_name": "Dr Example",
        "rendering_provider_npi": "1234567890",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["claim_number"] == "C9999"
    assert body["payer_claim_number"] == "PCN-1"
    assert body["rendering_provider_name"] == "Dr Example"
    assert body["rendering_provider_npi"] == "1234567890"


def test_patch_accepts_status_and_notes(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={
        "status": "paid", "notes": "manual review done",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "paid"
    assert body["notes"] == "manual review done"


def test_patch_bad_status_enum_422(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={"status": "not_a_status"})
    assert r.status_code == 422


def test_patch_bad_insurance_order_enum_422(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={"insurance_order": "quaternary"})
    assert r.status_code == 422


def test_patch_nonexistent_patient_id_422(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}",
                     json={"patient_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 422


def test_patch_valid_patient_id_succeeds(client, db):
    c = _seed_claim(db)
    p = Patient(patient_id="P001", first_name="A", last_name="B")
    db.add(p)
    db.commit()
    db.refresh(p)
    r = client.patch(f"/api/claims/{c.id}", json={"patient_id": str(p.id)})
    assert r.status_code == 200
    assert r.json()["patient_id"] == str(p.id)


def test_patch_missing_claim_404(client, db):
    r = client.patch("/api/claims/00000000-0000-0000-0000-000000000000",
                     json={"notes": "x"})
    assert r.status_code == 404


def test_patch_audit_row_has_changed_fields_only(client, db):
    c = _seed_claim(db)
    client.patch(f"/api/claims/{c.id}", json={"notes": "hello"})
    entries = db.query(AuditLog).filter(
        AuditLog.resource_type == "claim",
        AuditLog.action == "UPDATE",
        AuditLog.resource_id == str(c.id),
    ).all()
    assert len(entries) == 1
    e = entries[0]
    assert set(e.new_values.keys()) == {"notes"}
    assert e.new_values["notes"] == "hello"
    assert "notes" in e.old_values


def test_patch_forbidden_for_clinical(clinical_client, db):
    c = _seed_claim(db)
    r = clinical_client.patch(f"/api/claims/{c.id}", json={"notes": "x"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claim_edit.py -v 2>&1 | tail -25
```
Expected: most tests FAIL — the existing PATCH only allows 8 fields so money/dates/identifiers/provider tests return 200 but the fields aren't persisted (body reflects the pre-patch value). The enum-validation and balance-recompute tests fail outright. The 404 and forbidden tests may already pass.

- [ ] **Step 3: Rewrite the PATCH handler in `claims.py`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/claims.py`.

**Imports to add/modify at the top of the file:**

1. Add two new `import` lines alongside the existing stdlib imports (after line 5 `import uuid`):
```python
from datetime import date as date_cls
from decimal import Decimal, InvalidOperation
```

2. Replace the existing line 8 (`from app.models.claim import Claim, ClaimStatus, EraFile`) with:
```python
from app.models.claim import Claim, ClaimStatus, InsuranceOrder, EraFile
```

3. Add two new `from` lines below the existing model imports:
```python
from app.models.patient import Patient
from app.services.claim_math import recompute_balance
```

**Then replace the current `update_claim` function (lines 82-96) with the following (and add the `EDITABLE_CLAIM_FIELDS`/helpers as module-level code just above `update_claim`):**

```python
EDITABLE_CLAIM_FIELDS = {
    # strings
    "claim_number", "payer_claim_number", "payer_name", "payer_id",
    "subscriber_id", "group_number", "check_number",
    "rendering_provider_name", "rendering_provider_npi", "notes",
    # enums
    "status", "insurance_order",
    # dates
    "date_of_service_from", "date_of_service_to", "check_date",
    # money
    "billed_amount", "allowed_amount", "paid_amount",
    "patient_responsibility", "contractual_adjustment", "other_adjustment",
    # relation
    "patient_id",
}

MONEY_FIELDS = {
    "billed_amount", "allowed_amount", "paid_amount",
    "patient_responsibility", "contractual_adjustment", "other_adjustment",
}

DATE_FIELDS = {"date_of_service_from", "date_of_service_to", "check_date"}


def _coerce_claim_value(k: str, v):
    """Coerce incoming JSON value to the type the ORM column expects."""
    if v is None:
        return None
    if k == "status":
        try:
            return ClaimStatus(v)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid status: {v}")
    if k == "insurance_order":
        try:
            return InsuranceOrder(v)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid insurance_order: {v}")
    if k in MONEY_FIELDS:
        try:
            return Decimal(str(v))
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"invalid number for {k}: {v!r}")
    if k in DATE_FIELDS:
        if isinstance(v, str):
            try:
                return date_cls.fromisoformat(v)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"invalid date for {k}: {v!r}")
        return v
    return v


@router.patch("/{claim_id}")
def update_claim(claim_id: str, data: dict, db: Session = Depends(get_db)):
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    # Validate patient_id exists (if provided and not null)
    if "patient_id" in data and data["patient_id"]:
        if not db.query(Patient).filter(Patient.id == data["patient_id"]).first():
            raise HTTPException(status_code=422, detail="patient_id does not exist")

    old = {}
    new = {}
    for k, raw in data.items():
        if k not in EDITABLE_CLAIM_FIELDS:
            continue  # silently drop balance, era_file_id, etc.
        if not hasattr(claim, k):
            continue
        v = _coerce_claim_value(k, raw)
        current = getattr(claim, k)
        if current != v:
            # Capture before/after — stringify enums/decimals/dates for JSON audit
            old[k] = _audit_val(current)
            new[k] = _audit_val(v)
            setattr(claim, k, v)

    if any(k in new for k in MONEY_FIELDS):
        recompute_balance(claim)

    db.commit()
    if old or new:
        log_action(db, "UPDATE", "claim", resource_id=claim_id,
                   old_values=old, new_values=new)
    db.refresh(claim)
    return _claim_to_dict(claim, detailed=True)


def _audit_val(v):
    if v is None:
        return None
    if hasattr(v, "value"):  # enum
        return v.value
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date_cls):
        return v.isoformat()
    return v
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claim_edit.py tests/ -v 2>&1 | tail -25
```
Expected: 13 new claim_edit tests PASS + all prior tests still PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/claims.py backend/tests/test_claim_edit.py
git commit -m "feat(backend): PATCH /claims/{id} full allow-list + auto balance"
```

---

## Task 3: Backend — `service_lines` router (POST/PATCH/DELETE)

**Files:**
- Create: `backend/app/routers/service_lines.py`
- Create: `backend/tests/test_service_lines.py`
- Modify: `backend/app/main.py` (include router with BILLING guard)

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_service_lines.py`:

```python
"""Tests for service-line CRUD endpoints."""
from decimal import Decimal
from app.models.claim import Claim, ServiceLine, ServiceLineAdjustment, ClaimStatus
from app.models.audit import AuditLog


def _seed_claim(db) -> Claim:
    c = Claim(
        claim_number="C-SL",
        status=ClaimStatus.PENDING,
        billed_amount=Decimal("100"),
        balance=Decimal("100"),
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _seed_line(db, claim_id) -> ServiceLine:
    sl = ServiceLine(
        claim_id=claim_id,
        procedure_code="99213",
        units=Decimal("1"),
        billed_amount=Decimal("50"),
    )
    db.add(sl); db.commit(); db.refresh(sl)
    return sl


def test_post_service_line_full_fields(client, db):
    c = _seed_claim(db)
    r = client.post(f"/api/claims/{c.id}/service-lines", json={
        "procedure_code": "99213",
        "modifier_1": "25",
        "revenue_code": "0450",
        "units": 2,
        "description": "visit",
        "date_of_service_from": "2026-01-15",
        "date_of_service_to": "2026-01-15",
        "billed_amount": 150,
        "allowed_amount": 120,
        "paid_amount": 100,
        "patient_responsibility": 20,
        "contractual_adjustment": 30,
        "other_adjustment": 0,
        "diagnosis_codes": ["Z00.00", "E11.9"],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["procedure_code"] == "99213"
    assert body["modifier_1"] == "25"
    assert float(body["billed_amount"]) == 150.0
    assert body["date_of_service_from"] == "2026-01-15"
    assert "id" in body


def test_post_service_line_empty_body_creates_blank(client, db):
    c = _seed_claim(db)
    r = client.post(f"/api/claims/{c.id}/service-lines", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["procedure_code"] is None


def test_post_service_line_missing_claim_404(client, db):
    r = client.post("/api/claims/00000000-0000-0000-0000-000000000000/service-lines",
                    json={"procedure_code": "99213"})
    assert r.status_code == 404


def test_patch_service_line_updates_fields(client, db):
    c = _seed_claim(db)
    sl = _seed_line(db, c.id)
    r = client.patch(f"/api/service-lines/{sl.id}",
                     json={"modifier_1": "59", "units": 3, "billed_amount": 75})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["modifier_1"] == "59"
    assert float(body["units"]) == 3.0
    assert float(body["billed_amount"]) == 75.0


def test_patch_service_line_missing_404(client, db):
    r = client.patch("/api/service-lines/00000000-0000-0000-0000-000000000000",
                     json={"units": 2})
    assert r.status_code == 404


def test_delete_service_line_cascades_adjustments(client, db):
    c = _seed_claim(db)
    sl = _seed_line(db, c.id)
    # seed two SL adjustments
    db.add_all([
        ServiceLineAdjustment(service_line_id=sl.id, group_code="CO",
                              reason_code="45", amount=Decimal("10")),
        ServiceLineAdjustment(service_line_id=sl.id, group_code="PR",
                              reason_code="1", amount=Decimal("5")),
    ])
    db.commit()

    r = client.delete(f"/api/service-lines/{sl.id}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert db.query(ServiceLine).filter(ServiceLine.id == sl.id).first() is None
    assert db.query(ServiceLineAdjustment).filter(
        ServiceLineAdjustment.service_line_id == sl.id).count() == 0


def test_delete_service_line_missing_404(client, db):
    r = client.delete("/api/service-lines/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_service_line_writes_audit_rows_per_op(client, db):
    c = _seed_claim(db)
    # POST
    r = client.post(f"/api/claims/{c.id}/service-lines",
                    json={"procedure_code": "99213"})
    assert r.status_code == 201
    new_id = r.json()["id"]

    # PATCH
    client.patch(f"/api/service-lines/{new_id}", json={"units": 2})

    # DELETE
    client.delete(f"/api/service-lines/{new_id}")

    actions = [a.action for a in db.query(AuditLog).filter(
        AuditLog.resource_type == "service_line",
        AuditLog.resource_id == new_id,
    ).order_by(AuditLog.timestamp).all()]
    assert actions == ["CREATE", "UPDATE", "DELETE"]


def test_service_line_post_recomputes_parent_balance(client, db):
    c = _seed_claim(db)  # billed=100, balance=100
    # Change claim money first so balance ≠ default
    c.paid_amount = Decimal("40")
    db.commit()
    # Posting an SL should NOT change the claim money; balance should
    # re-settle to billed - paid = 60 on recompute.
    client.post(f"/api/claims/{c.id}/service-lines", json={"procedure_code": "99213"})
    db.refresh(c)
    assert float(c.balance) == 60.0


def test_service_lines_forbidden_for_clinical(clinical_client, db):
    c = _seed_claim(db)
    r = clinical_client.post(f"/api/claims/{c.id}/service-lines", json={})
    assert r.status_code == 403
    sl = _seed_line(db, c.id)
    assert clinical_client.patch(f"/api/service-lines/{sl.id}",
                                 json={"units": 2}).status_code == 403
    assert clinical_client.delete(f"/api/service-lines/{sl.id}").status_code == 403
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_service_lines.py -v 2>&1 | tail -20
```
Expected: 10 tests FAIL with 404 (endpoints don't exist yet).

- [ ] **Step 3: Create `backend/app/routers/service_lines.py`**

```python
"""Service-line CRUD — nested under claims for POST, flat for PATCH/DELETE."""
from datetime import date as date_cls
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.claim import Claim, ServiceLine
from app.services.audit_service import log_action
from app.services.claim_math import recompute_balance

router = APIRouter(tags=["service-lines"])

EDITABLE_SL_FIELDS = {
    "procedure_code", "modifier_1", "modifier_2", "modifier_3", "modifier_4",
    "revenue_code", "units", "description",
    "date_of_service_from", "date_of_service_to",
    "billed_amount", "allowed_amount", "paid_amount",
    "patient_responsibility", "contractual_adjustment", "other_adjustment",
    "diagnosis_codes",
}

SL_MONEY_FIELDS = {
    "billed_amount", "allowed_amount", "paid_amount",
    "patient_responsibility", "contractual_adjustment", "other_adjustment",
}

SL_DATE_FIELDS = {"date_of_service_from", "date_of_service_to"}

SL_NUMERIC_FIELDS = SL_MONEY_FIELDS | {"units"}


def _coerce_sl_value(k: str, v):
    if v is None:
        return None
    if k in SL_NUMERIC_FIELDS:
        try:
            return Decimal(str(v))
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"invalid number for {k}: {v!r}")
    if k in SL_DATE_FIELDS:
        if isinstance(v, str):
            try:
                return date_cls.fromisoformat(v)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"invalid date for {k}: {v!r}")
    if k == "diagnosis_codes":
        if not isinstance(v, list):
            raise HTTPException(status_code=422, detail="diagnosis_codes must be a list")
    return v


def _audit_val(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date_cls):
        return v.isoformat()
    return v


def _serialize(sl: ServiceLine) -> dict:
    return {
        "id": str(sl.id),
        "claim_id": str(sl.claim_id) if sl.claim_id else None,
        "procedure_code": sl.procedure_code,
        "modifier_1": sl.modifier_1,
        "modifier_2": sl.modifier_2,
        "modifier_3": sl.modifier_3,
        "modifier_4": sl.modifier_4,
        "revenue_code": sl.revenue_code,
        "units": float(sl.units) if sl.units is not None else None,
        "description": sl.description,
        "date_of_service_from": sl.date_of_service_from.isoformat() if sl.date_of_service_from else None,
        "date_of_service_to": sl.date_of_service_to.isoformat() if sl.date_of_service_to else None,
        "billed_amount": float(sl.billed_amount or 0),
        "allowed_amount": float(sl.allowed_amount or 0),
        "paid_amount": float(sl.paid_amount or 0),
        "patient_responsibility": float(sl.patient_responsibility or 0),
        "contractual_adjustment": float(sl.contractual_adjustment or 0),
        "other_adjustment": float(sl.other_adjustment or 0),
        "diagnosis_codes": sl.diagnosis_codes or [],
    }


@router.post("/claims/{claim_id}/service-lines", status_code=201)
def create_service_line(claim_id: str, data: dict, db: Session = Depends(get_db)):
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    sl = ServiceLine(claim_id=claim.id)
    new = {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_SL_FIELDS:
            continue
        v = _coerce_sl_value(k, raw)
        setattr(sl, k, v)
        new[k] = _audit_val(v)

    db.add(sl)
    recompute_balance(claim)
    db.commit()
    db.refresh(sl)
    log_action(db, "CREATE", "service_line",
               resource_id=str(sl.id), new_values=new)
    return _serialize(sl)


@router.patch("/service-lines/{line_id}")
def update_service_line(line_id: str, data: dict, db: Session = Depends(get_db)):
    sl = db.query(ServiceLine).filter(ServiceLine.id == line_id).first()
    if not sl:
        raise HTTPException(status_code=404, detail="Service line not found")

    old, new = {}, {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_SL_FIELDS:
            continue
        v = _coerce_sl_value(k, raw)
        cur = getattr(sl, k)
        if cur != v:
            old[k] = _audit_val(cur)
            new[k] = _audit_val(v)
            setattr(sl, k, v)

    claim = db.query(Claim).filter(Claim.id == sl.claim_id).first()
    if claim is not None:
        recompute_balance(claim)

    db.commit()
    if old or new:
        log_action(db, "UPDATE", "service_line",
                   resource_id=line_id, old_values=old, new_values=new)
    db.refresh(sl)
    return _serialize(sl)


@router.delete("/service-lines/{line_id}")
def delete_service_line(line_id: str, db: Session = Depends(get_db)):
    sl = db.query(ServiceLine).filter(ServiceLine.id == line_id).first()
    if not sl:
        raise HTTPException(status_code=404, detail="Service line not found")

    claim_id = sl.claim_id
    db.delete(sl)
    db.flush()

    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if claim is not None:
        recompute_balance(claim)

    db.commit()
    log_action(db, "DELETE", "service_line", resource_id=line_id)
    return {"ok": True}
```

- [ ] **Step 4: Wire router into `main.py`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/main.py`.

Update the router imports line to include `service_lines`:
```python
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch, admin_users, service_lines
```

Add `include_router` call directly under the existing `claims.router` line (so they live together):
```python
app.include_router(claims.router, prefix="/api", dependencies=BILLING)
app.include_router(service_lines.router, prefix="/api", dependencies=BILLING)
```

- [ ] **Step 5: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_service_lines.py tests/ -v 2>&1 | tail -25
```
Expected: 10 new tests PASS + all prior tests still PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/service_lines.py backend/app/main.py backend/tests/test_service_lines.py
git commit -m "feat(backend): service_lines router — POST/PATCH/DELETE with cascade + audit"
```

---

## Task 4: Backend — `claim_adjustments` router (POST/PATCH/DELETE)

**Files:**
- Create: `backend/app/routers/claim_adjustments.py`
- Create: `backend/tests/test_claim_adjustments.py`
- Modify: `backend/app/main.py` (include router)

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_claim_adjustments.py`:

```python
"""Tests for claim-level adjustment CRUD."""
from decimal import Decimal
from app.models.claim import Claim, ClaimAdjustment, ClaimStatus
from app.models.audit import AuditLog


def _seed_claim(db) -> Claim:
    c = Claim(claim_number="C-ADJ", status=ClaimStatus.PENDING,
              billed_amount=Decimal("100"), balance=Decimal("100"))
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_post_claim_adjustment_creates_row(client, db):
    c = _seed_claim(db)
    r = client.post(f"/api/claims/{c.id}/adjustments", json={
        "group_code": "CO",
        "reason_code": "45",
        "amount": 25,
        "reason_description": "Charge exceeds fee schedule",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["group_code"] == "CO"
    assert body["reason_code"] == "45"
    assert float(body["amount"]) == 25.0
    assert "id" in body


def test_post_claim_adjustment_does_not_change_claim_balance(client, db):
    c = _seed_claim(db)  # balance=100 (no other money edits)
    client.post(f"/api/claims/{c.id}/adjustments",
                json={"group_code": "CO", "reason_code": "45", "amount": 25})
    db.refresh(c)
    # Key freeform-behavior assertion: adjustment CRUD must not touch balance.
    assert float(c.balance) == 100.0


def test_post_claim_adjustment_missing_claim_404(client, db):
    r = client.post("/api/claims/00000000-0000-0000-0000-000000000000/adjustments",
                    json={"group_code": "CO", "reason_code": "45", "amount": 1})
    assert r.status_code == 404


def test_patch_claim_adjustment_updates_fields(client, db):
    c = _seed_claim(db)
    adj = ClaimAdjustment(claim_id=c.id, group_code="CO", reason_code="45",
                          amount=Decimal("10"))
    db.add(adj); db.commit(); db.refresh(adj)
    r = client.patch(f"/api/claim-adjustments/{adj.id}",
                     json={"reason_code": "97", "amount": 15,
                           "reason_description": "Not covered"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reason_code"] == "97"
    assert float(body["amount"]) == 15.0
    assert body["reason_description"] == "Not covered"


def test_patch_claim_adjustment_missing_404(client, db):
    r = client.patch("/api/claim-adjustments/00000000-0000-0000-0000-000000000000",
                     json={"amount": 1})
    assert r.status_code == 404


def test_delete_claim_adjustment_removes_row(client, db):
    c = _seed_claim(db)
    adj = ClaimAdjustment(claim_id=c.id, group_code="CO",
                          reason_code="45", amount=Decimal("10"))
    db.add(adj); db.commit(); db.refresh(adj)
    r = client.delete(f"/api/claim-adjustments/{adj.id}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert db.query(ClaimAdjustment).filter(ClaimAdjustment.id == adj.id).first() is None


def test_delete_claim_adjustment_missing_404(client, db):
    r = client.delete("/api/claim-adjustments/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_claim_adjustment_audit_rows_written(client, db):
    c = _seed_claim(db)
    r = client.post(f"/api/claims/{c.id}/adjustments",
                    json={"group_code": "CO", "reason_code": "45", "amount": 10})
    adj_id = r.json()["id"]
    client.patch(f"/api/claim-adjustments/{adj_id}", json={"amount": 12})
    client.delete(f"/api/claim-adjustments/{adj_id}")
    actions = [a.action for a in db.query(AuditLog).filter(
        AuditLog.resource_type == "claim_adjustment",
        AuditLog.resource_id == adj_id,
    ).order_by(AuditLog.timestamp).all()]
    assert actions == ["CREATE", "UPDATE", "DELETE"]


def test_claim_adjustments_forbidden_for_clinical(clinical_client, db):
    c = _seed_claim(db)
    assert clinical_client.post(
        f"/api/claims/{c.id}/adjustments",
        json={"group_code": "CO", "reason_code": "45", "amount": 1}
    ).status_code == 403
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claim_adjustments.py -v 2>&1 | tail -15
```
Expected: 9 tests FAIL with 404.

- [ ] **Step 3: Create `backend/app/routers/claim_adjustments.py`**

```python
"""Claim-level adjustment CRUD (CARC-coded breakdown rows)."""
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.claim import Claim, ClaimAdjustment
from app.services.audit_service import log_action

router = APIRouter(tags=["claim-adjustments"])

EDITABLE_ADJ_FIELDS = {
    "group_code", "reason_code", "amount", "quantity", "reason_description",
}
ADJ_NUMERIC_FIELDS = {"amount", "quantity"}


def _coerce_adj_value(k: str, v):
    if v is None:
        return None
    if k in ADJ_NUMERIC_FIELDS:
        try:
            return Decimal(str(v))
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=422,
                                detail=f"invalid number for {k}: {v!r}")
    return v


def _audit_val(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


def _serialize(a: ClaimAdjustment) -> dict:
    return {
        "id": str(a.id),
        "claim_id": str(a.claim_id) if a.claim_id else None,
        "group_code": a.group_code,
        "reason_code": a.reason_code,
        "amount": float(a.amount or 0),
        "quantity": float(a.quantity) if a.quantity is not None else None,
        "reason_description": a.reason_description,
    }


@router.post("/claims/{claim_id}/adjustments", status_code=201)
def create_claim_adjustment(claim_id: str, data: dict, db: Session = Depends(get_db)):
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    adj = ClaimAdjustment(claim_id=claim.id)
    new = {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_ADJ_FIELDS:
            continue
        v = _coerce_adj_value(k, raw)
        setattr(adj, k, v)
        new[k] = _audit_val(v)

    db.add(adj)
    db.commit()
    db.refresh(adj)
    log_action(db, "CREATE", "claim_adjustment",
               resource_id=str(adj.id), new_values=new)
    return _serialize(adj)


@router.patch("/claim-adjustments/{adj_id}")
def update_claim_adjustment(adj_id: str, data: dict, db: Session = Depends(get_db)):
    adj = db.query(ClaimAdjustment).filter(ClaimAdjustment.id == adj_id).first()
    if not adj:
        raise HTTPException(status_code=404, detail="Claim adjustment not found")

    old, new = {}, {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_ADJ_FIELDS:
            continue
        v = _coerce_adj_value(k, raw)
        cur = getattr(adj, k)
        if cur != v:
            old[k] = _audit_val(cur)
            new[k] = _audit_val(v)
            setattr(adj, k, v)

    db.commit()
    if old or new:
        log_action(db, "UPDATE", "claim_adjustment",
                   resource_id=adj_id, old_values=old, new_values=new)
    db.refresh(adj)
    return _serialize(adj)


@router.delete("/claim-adjustments/{adj_id}")
def delete_claim_adjustment(adj_id: str, db: Session = Depends(get_db)):
    adj = db.query(ClaimAdjustment).filter(ClaimAdjustment.id == adj_id).first()
    if not adj:
        raise HTTPException(status_code=404, detail="Claim adjustment not found")
    db.delete(adj)
    db.commit()
    log_action(db, "DELETE", "claim_adjustment", resource_id=adj_id)
    return {"ok": True}
```

- [ ] **Step 4: Wire router into `main.py`**

Update the imports line:
```python
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch, admin_users, service_lines, claim_adjustments
```

Add `include_router` directly under `service_lines`:
```python
app.include_router(service_lines.router, prefix="/api", dependencies=BILLING)
app.include_router(claim_adjustments.router, prefix="/api", dependencies=BILLING)
```

- [ ] **Step 5: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claim_adjustments.py tests/ -v 2>&1 | tail -20
```
Expected: 9 new tests PASS + all prior tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/claim_adjustments.py backend/app/main.py backend/tests/test_claim_adjustments.py
git commit -m "feat(backend): claim_adjustments router — full CRUD + audit"
```

---

## Task 5: Backend — `service_line_adjustments` router (POST/PATCH/DELETE)

**Files:**
- Create: `backend/app/routers/service_line_adjustments.py`
- Create: `backend/tests/test_service_line_adjustments.py`
- Modify: `backend/app/main.py` (include router)

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_service_line_adjustments.py`:

```python
"""Tests for service-line-level adjustment CRUD."""
from decimal import Decimal
from app.models.claim import Claim, ServiceLine, ServiceLineAdjustment, ClaimStatus
from app.models.audit import AuditLog


def _seed(db):
    c = Claim(claim_number="C-SLA", status=ClaimStatus.PENDING,
              billed_amount=Decimal("100"), balance=Decimal("100"))
    db.add(c); db.commit(); db.refresh(c)
    sl = ServiceLine(claim_id=c.id, procedure_code="99213",
                     units=Decimal("1"), billed_amount=Decimal("50"))
    db.add(sl); db.commit(); db.refresh(sl)
    return c, sl


def test_post_sl_adjustment_creates(client, db):
    _, sl = _seed(db)
    r = client.post(f"/api/service-lines/{sl.id}/adjustments", json={
        "group_code": "PR", "reason_code": "1", "amount": 20,
        "reason_description": "Deductible",
    })
    assert r.status_code == 201, r.text
    assert r.json()["group_code"] == "PR"
    assert float(r.json()["amount"]) == 20.0


def test_post_sl_adjustment_does_not_change_claim_balance(client, db):
    c, sl = _seed(db)  # claim.balance = 100
    client.post(f"/api/service-lines/{sl.id}/adjustments",
                json={"group_code": "PR", "reason_code": "1", "amount": 20})
    db.refresh(c)
    assert float(c.balance) == 100.0


def test_post_sl_adjustment_missing_line_404(client, db):
    r = client.post("/api/service-lines/00000000-0000-0000-0000-000000000000/adjustments",
                    json={"group_code": "PR", "reason_code": "1", "amount": 1})
    assert r.status_code == 404


def test_patch_sl_adjustment_updates(client, db):
    _, sl = _seed(db)
    adj = ServiceLineAdjustment(service_line_id=sl.id, group_code="CO",
                                reason_code="45", amount=Decimal("10"))
    db.add(adj); db.commit(); db.refresh(adj)
    r = client.patch(f"/api/service-line-adjustments/{adj.id}",
                     json={"amount": 12, "reason_description": "updated"})
    assert r.status_code == 200
    assert float(r.json()["amount"]) == 12.0
    assert r.json()["reason_description"] == "updated"


def test_patch_sl_adjustment_missing_404(client, db):
    r = client.patch("/api/service-line-adjustments/00000000-0000-0000-0000-000000000000",
                     json={"amount": 1})
    assert r.status_code == 404


def test_delete_sl_adjustment_removes(client, db):
    _, sl = _seed(db)
    adj = ServiceLineAdjustment(service_line_id=sl.id, group_code="CO",
                                reason_code="45", amount=Decimal("10"))
    db.add(adj); db.commit(); db.refresh(adj)
    r = client.delete(f"/api/service-line-adjustments/{adj.id}")
    assert r.status_code == 200
    assert db.query(ServiceLineAdjustment).filter(
        ServiceLineAdjustment.id == adj.id).first() is None


def test_delete_sl_adjustment_missing_404(client, db):
    r = client.delete("/api/service-line-adjustments/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_sl_adjustment_audit_rows_written(client, db):
    _, sl = _seed(db)
    r = client.post(f"/api/service-lines/{sl.id}/adjustments",
                    json={"group_code": "PR", "reason_code": "1", "amount": 20})
    adj_id = r.json()["id"]
    client.patch(f"/api/service-line-adjustments/{adj_id}", json={"amount": 21})
    client.delete(f"/api/service-line-adjustments/{adj_id}")
    actions = [a.action for a in db.query(AuditLog).filter(
        AuditLog.resource_type == "service_line_adjustment",
        AuditLog.resource_id == adj_id,
    ).order_by(AuditLog.timestamp).all()]
    assert actions == ["CREATE", "UPDATE", "DELETE"]


def test_sl_adjustments_forbidden_for_clinical(clinical_client, db):
    _, sl = _seed(db)
    assert clinical_client.post(
        f"/api/service-lines/{sl.id}/adjustments",
        json={"group_code": "PR", "reason_code": "1", "amount": 1}
    ).status_code == 403
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_service_line_adjustments.py -v 2>&1 | tail -15
```
Expected: 9 tests FAIL with 404.

- [ ] **Step 3: Create `backend/app/routers/service_line_adjustments.py`**

```python
"""Service-line-level adjustment CRUD (CARC-coded breakdown rows per SL)."""
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.claim import ServiceLine, ServiceLineAdjustment
from app.services.audit_service import log_action

router = APIRouter(tags=["service-line-adjustments"])

EDITABLE_SLA_FIELDS = {
    "group_code", "reason_code", "amount", "quantity", "reason_description",
}
SLA_NUMERIC_FIELDS = {"amount", "quantity"}


def _coerce_sla_value(k: str, v):
    if v is None:
        return None
    if k in SLA_NUMERIC_FIELDS:
        try:
            return Decimal(str(v))
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=422,
                                detail=f"invalid number for {k}: {v!r}")
    return v


def _audit_val(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


def _serialize(a: ServiceLineAdjustment) -> dict:
    return {
        "id": str(a.id),
        "service_line_id": str(a.service_line_id) if a.service_line_id else None,
        "group_code": a.group_code,
        "reason_code": a.reason_code,
        "amount": float(a.amount or 0),
        "quantity": float(a.quantity) if a.quantity is not None else None,
        "reason_description": a.reason_description,
    }


@router.post("/service-lines/{line_id}/adjustments", status_code=201)
def create_sl_adjustment(line_id: str, data: dict, db: Session = Depends(get_db)):
    sl = db.query(ServiceLine).filter(ServiceLine.id == line_id).first()
    if not sl:
        raise HTTPException(status_code=404, detail="Service line not found")

    adj = ServiceLineAdjustment(service_line_id=sl.id)
    new = {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_SLA_FIELDS:
            continue
        v = _coerce_sla_value(k, raw)
        setattr(adj, k, v)
        new[k] = _audit_val(v)

    db.add(adj)
    db.commit()
    db.refresh(adj)
    log_action(db, "CREATE", "service_line_adjustment",
               resource_id=str(adj.id), new_values=new)
    return _serialize(adj)


@router.patch("/service-line-adjustments/{adj_id}")
def update_sl_adjustment(adj_id: str, data: dict, db: Session = Depends(get_db)):
    adj = db.query(ServiceLineAdjustment).filter(
        ServiceLineAdjustment.id == adj_id).first()
    if not adj:
        raise HTTPException(status_code=404, detail="Service line adjustment not found")

    old, new = {}, {}
    for k, raw in (data or {}).items():
        if k not in EDITABLE_SLA_FIELDS:
            continue
        v = _coerce_sla_value(k, raw)
        cur = getattr(adj, k)
        if cur != v:
            old[k] = _audit_val(cur)
            new[k] = _audit_val(v)
            setattr(adj, k, v)

    db.commit()
    if old or new:
        log_action(db, "UPDATE", "service_line_adjustment",
                   resource_id=adj_id, old_values=old, new_values=new)
    db.refresh(adj)
    return _serialize(adj)


@router.delete("/service-line-adjustments/{adj_id}")
def delete_sl_adjustment(adj_id: str, db: Session = Depends(get_db)):
    adj = db.query(ServiceLineAdjustment).filter(
        ServiceLineAdjustment.id == adj_id).first()
    if not adj:
        raise HTTPException(status_code=404, detail="Service line adjustment not found")
    db.delete(adj)
    db.commit()
    log_action(db, "DELETE", "service_line_adjustment", resource_id=adj_id)
    return {"ok": True}
```

- [ ] **Step 4: Wire into `main.py`**

Update imports line:
```python
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch, admin_users, service_lines, claim_adjustments, service_line_adjustments
```

Add `include_router` under `claim_adjustments`:
```python
app.include_router(claim_adjustments.router, prefix="/api", dependencies=BILLING)
app.include_router(service_line_adjustments.router, prefix="/api", dependencies=BILLING)
```

- [ ] **Step 5: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ -v 2>&1 | tail -25
```
Expected: 9 new tests PASS + all prior tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/service_line_adjustments.py backend/app/main.py backend/tests/test_service_line_adjustments.py
git commit -m "feat(backend): service_line_adjustments router — full CRUD + audit"
```

---

## Task 6: Frontend — `MoneyInput` shared primitive

**Files:**
- Create: `frontend/src/components/MoneyInput.jsx`

- [ ] **Step 1: Create the component**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/components/MoneyInput.jsx`:

```jsx
import React from 'react'

/**
 * Money input with a $ prefix and 2-decimal step.
 *
 * Props:
 * - value: number | string | null/undefined
 * - onChange: (newValue: string) => void
 * - disabled?: boolean
 * - placeholder?: string
 * - className?: string — additional classes merged onto the <input>
 */
export default function MoneyInput({ value, onChange, disabled, placeholder, className = '' }) {
  const displayValue = value === null || value === undefined ? '' : String(value)
  return (
    <div className="relative">
      <span className="absolute left-2 top-1/2 -translate-y-1/2 text-muted text-[12px] pointer-events-none">$</span>
      <input
        type="number"
        step="0.01"
        inputMode="decimal"
        className={`input w-full pl-5 py-1 text-[12px] font-mono ${className}`}
        value={displayValue}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder={placeholder}
      />
    </div>
  )
}
```

- [ ] **Step 2: Smoke-verify it imports (no tests — repo has no RTL setup)**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -10
```
Expected: build succeeds. (`MoneyInput` is unused by anything yet — Vite tree-shakes; the point is no syntax errors.)

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/components/MoneyInput.jsx
git commit -m "feat(frontend): MoneyInput shared component (\$ prefix, decimal step)"
```

---

## Task 7: Frontend — `PatientPicker` shared component

**Files:**
- Create: `frontend/src/components/PatientPicker.jsx`

- [ ] **Step 1: Create the component**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/components/PatientPicker.jsx`:

```jsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Autocomplete picker for Patients.
 * Props:
 * - value: patient id (uuid string) | null
 * - onChange: (newId: string | null) => void
 * - disabled?: boolean
 *
 * Shows the currently-selected patient's name + chart id; clicking opens
 * a small dropdown with a search input. Uses /api/patients?search=&per_page=10.
 */
export default function PatientPicker({ value, onChange, disabled }) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  const { data: current } = useQuery({
    queryKey: ['patient', value],
    queryFn: () => api.get(`/patients/${value}`).then(r => r.data),
    enabled: !!value,
  })

  const { data: results = [], isFetching } = useQuery({
    queryKey: ['patients-search', search],
    queryFn: () => api.get('/patients', { params: { search, per_page: 10 } })
      .then(r => r.data.patients || r.data),
    enabled: open && search.length >= 2,
    staleTime: 10_000,
  })

  function pick(p) {
    onChange(p.id)
    setOpen(false)
    setSearch('')
  }

  const label = current
    ? `${current.last_name || ''}, ${current.first_name || ''} (${current.patient_id || '—'})`
    : (value ? 'Loading…' : '— no patient —')

  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        className="input w-full py-1 text-left text-[12px]"
        onClick={() => setOpen(v => !v)}
      >
        {label}
      </button>
      {open && (
        <div className="absolute z-10 mt-1 w-full card p-2 shadow-lg bg-white">
          <input
            autoFocus
            className="input w-full py-1 text-[12px]"
            placeholder="Search by name or chart #…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {value && (
            <button
              type="button"
              className="mt-1 text-[11px] text-muted underline"
              onClick={() => { onChange(null); setOpen(false) }}
            >
              Clear selection
            </button>
          )}
          <div className="mt-2 max-h-48 overflow-y-auto">
            {isFetching && <div className="text-[11px] text-muted">Searching…</div>}
            {!isFetching && search.length < 2 && (
              <div className="text-[11px] text-muted">Type 2+ characters to search.</div>
            )}
            {!isFetching && search.length >= 2 && results.length === 0 && (
              <div className="text-[11px] text-muted">No matches.</div>
            )}
            {results.map(p => (
              <button
                key={p.id}
                type="button"
                className="block w-full text-left px-1 py-1 hover:bg-plum-50 text-[12px]"
                onClick={() => pick(p)}
              >
                <span className="font-mono text-muted">{p.patient_id || '—'}</span>{' · '}
                {p.last_name}, {p.first_name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Smoke-verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -10
```
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/components/PatientPicker.jsx
git commit -m "feat(frontend): PatientPicker — autocomplete against /api/patients"
```

---

## Task 8: Frontend — `AdjustmentList` shared component

**Files:**
- Create: `frontend/src/components/AdjustmentList.jsx`

- [ ] **Step 1: Create the component**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/components/AdjustmentList.jsx`:

```jsx
import { useState } from 'react'
import MoneyInput from './MoneyInput'

/**
 * Editable list of adjustments (CARC-coded breakdown rows).
 *
 * Works for both claim and service-line adjustments. The parent tracks the
 * full array; this component renders rows, inline-edits them, and flags ops.
 *
 * Props:
 * - value: Array<{ id?: string, tempId?: number, op: 'none'|'edited'|'deleted'|'new',
 *                  group_code, reason_code, amount, reason_description, quantity? }>
 * - onChange: (newArray) => void
 * - disabled?: boolean
 */
export default function AdjustmentList({ value, onChange, disabled }) {
  const [nextTempId, setNextTempId] = useState(1)

  function updateRow(idx, patch) {
    const next = value.slice()
    const row = { ...next[idx], ...patch }
    if (row.op === 'none' || row.op === 'edited') {
      row.op = 'edited'
    }
    next[idx] = row
    onChange(next)
  }

  function markDeleted(idx) {
    const next = value.slice()
    if (next[idx].op === 'new') {
      next.splice(idx, 1)  // never sent to server — just drop it
    } else {
      next[idx] = { ...next[idx], op: 'deleted' }
    }
    onChange(next)
  }

  function undoDelete(idx) {
    const next = value.slice()
    next[idx] = { ...next[idx], op: 'none' }
    onChange(next)
  }

  function addRow() {
    onChange([
      ...value,
      {
        tempId: nextTempId, op: 'new',
        group_code: '', reason_code: '', amount: 0, reason_description: '',
      },
    ])
    setNextTempId(n => n + 1)
  }

  const visible = value.filter(r => r.op !== 'deleted')
  const deleted = value.map((r, i) => ({ ...r, _i: i })).filter(r => r.op === 'deleted')

  return (
    <div className="space-y-1">
      {visible.length === 0 && deleted.length === 0 && (
        <div className="text-[11px] text-muted italic">No adjustments.</div>
      )}

      {value.map((row, idx) => {
        if (row.op === 'deleted') return null
        return (
          <div key={row.id || `new-${row.tempId}`} className="flex gap-1 items-center">
            <input
              className="input w-14 py-0.5 text-[11px] font-mono"
              placeholder="CO"
              value={row.group_code || ''}
              onChange={(e) => updateRow(idx, { group_code: e.target.value })}
              disabled={disabled}
            />
            <input
              className="input w-16 py-0.5 text-[11px] font-mono"
              placeholder="45"
              value={row.reason_code || ''}
              onChange={(e) => updateRow(idx, { reason_code: e.target.value })}
              disabled={disabled}
            />
            <div className="w-24">
              <MoneyInput
                value={row.amount ?? 0}
                onChange={(v) => updateRow(idx, { amount: v })}
                disabled={disabled}
              />
            </div>
            <input
              className="input flex-1 py-0.5 text-[11px]"
              placeholder="Description"
              value={row.reason_description || ''}
              onChange={(e) => updateRow(idx, { reason_description: e.target.value })}
              disabled={disabled}
            />
            <button
              type="button"
              className="text-[11px] text-danger px-1"
              onClick={() => markDeleted(idx)}
              disabled={disabled}
              title="Remove"
            >✗</button>
          </div>
        )
      })}

      {deleted.map(row => (
        <div key={`d-${row.id || row.tempId}`} className="flex items-center gap-2 text-[11px] text-muted line-through">
          <span>{row.group_code}-{row.reason_code} ${row.amount} {row.reason_description}</span>
          <button type="button" className="underline no-underline-hover"
                  onClick={() => undoDelete(row._i)}>undo</button>
        </div>
      ))}

      <button
        type="button"
        className="text-[11px] text-plum-600 underline mt-1"
        onClick={addRow}
        disabled={disabled}
      >+ Add adjustment</button>
    </div>
  )
}
```

- [ ] **Step 2: Smoke-verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -10
```
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/components/AdjustmentList.jsx
git commit -m "feat(frontend): AdjustmentList component (inline edit/add/delete with op tags)"
```

---

## Task 9: Frontend — `useClaimEdit` hook + `EditClaimDrawer` component

**Files:**
- Create: `frontend/src/hooks/useClaimEdit.js`
- Create: `frontend/src/components/EditClaimDrawer.jsx`

- [ ] **Step 1: Create the orchestration hook**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/hooks/useClaimEdit.js`:

```js
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Orchestrates a sequential save for the claim edit drawer.
 *
 * save({ claimId, fieldsDiff, adjustments }):
 *  - claim PATCH if fieldsDiff has keys
 *  - adjustments: POST all 'new' rows, PATCH all 'edited', DELETE all 'deleted'
 *
 * Exposes { save, saving, error, step } where `step` is a progress string
 * like "2/4" during execution.
 */
export function useClaimEdit() {
  const queryClient = useQueryClient()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [step, setStep] = useState(null)

  async function save({ claimId, fieldsDiff, adjustments }) {
    setSaving(true)
    setError(null)

    // Build ordered operation list
    const ops = []
    if (fieldsDiff && Object.keys(fieldsDiff).length > 0) {
      ops.push({ kind: 'claim-patch', body: fieldsDiff })
    }
    for (const a of adjustments) {
      if (a.op === 'new') ops.push({ kind: 'adj-post', body: _adjBody(a), tempId: a.tempId })
      else if (a.op === 'edited') ops.push({ kind: 'adj-patch', id: a.id, body: _adjBody(a) })
      else if (a.op === 'deleted') ops.push({ kind: 'adj-delete', id: a.id })
    }

    for (let i = 0; i < ops.length; i++) {
      const op = ops[i]
      setStep(`${i + 1}/${ops.length}`)
      try {
        if (op.kind === 'claim-patch') {
          await api.patch(`/claims/${claimId}`, op.body)
        } else if (op.kind === 'adj-post') {
          await api.post(`/claims/${claimId}/adjustments`, op.body)
        } else if (op.kind === 'adj-patch') {
          await api.patch(`/claim-adjustments/${op.id}`, op.body)
        } else if (op.kind === 'adj-delete') {
          await api.delete(`/claim-adjustments/${op.id}`)
        }
      } catch (e) {
        setError({
          message: e?.response?.data?.detail || e.message || 'Save failed',
          completed: i,
          total: ops.length,
          failedOp: op,
        })
        setSaving(false)
        queryClient.invalidateQueries({ queryKey: ['claim', claimId] })
        return { ok: false, completed: i, total: ops.length }
      }
    }

    setSaving(false)
    setStep(null)
    queryClient.invalidateQueries({ queryKey: ['claim', claimId] })
    return { ok: true, completed: ops.length, total: ops.length }
  }

  function reset() { setError(null); setStep(null) }

  return { save, saving, error, step, reset }
}

function _adjBody(a) {
  const out = {
    group_code: a.group_code,
    reason_code: a.reason_code,
    amount: a.amount,
    reason_description: a.reason_description,
  }
  if (a.quantity !== undefined && a.quantity !== null && a.quantity !== '') {
    out.quantity = a.quantity
  }
  return out
}
```

- [ ] **Step 2: Create the drawer component**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/components/EditClaimDrawer.jsx`:

```jsx
import { useEffect, useMemo, useState } from 'react'
import MoneyInput from './MoneyInput'
import PatientPicker from './PatientPicker'
import AdjustmentList from './AdjustmentList'
import { useClaimEdit } from '../hooks/useClaimEdit'

const CLAIM_STATUSES = [
  'pending', 'paid', 'partial', 'denied', 'adjusted', 'reversed', 'appealed', 'written_off'
]
const INSURANCE_ORDERS = ['primary', 'secondary', 'tertiary', 'patient']

const EDITABLE_CLAIM_FIELDS = [
  'claim_number', 'payer_claim_number', 'payer_name', 'payer_id',
  'subscriber_id', 'group_number', 'insurance_order',
  'date_of_service_from', 'date_of_service_to',
  'check_number', 'check_date',
  'rendering_provider_name', 'rendering_provider_npi',
  'patient_id',
  'status', 'notes',
  'billed_amount', 'allowed_amount', 'paid_amount',
  'patient_responsibility', 'contractual_adjustment', 'other_adjustment',
]


export default function EditClaimDrawer({ claim, onClose }) {
  const initialFields = useMemo(() => {
    const o = {}
    for (const k of EDITABLE_CLAIM_FIELDS) o[k] = claim[k] ?? null
    return o
  }, [claim])

  const [fields, setFields] = useState(initialFields)
  const [adjustments, setAdjustments] = useState(
    (claim.adjustments || []).map(a => ({ ...a, op: 'none' }))
  )
  const { save, saving, error, step, reset } = useClaimEdit()

  useEffect(() => { document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = '' } }, [])

  function set(k, v) {
    setFields(prev => ({ ...prev, [k]: v }))
  }

  const computedBalance = useMemo(() => {
    const n = (v) => parseFloat(v || 0) || 0
    return n(fields.billed_amount) - n(fields.contractual_adjustment) - n(fields.other_adjustment)
         - n(fields.paid_amount) - n(fields.patient_responsibility)
  }, [fields])

  function diffFields() {
    const out = {}
    for (const k of EDITABLE_CLAIM_FIELDS) {
      if ((fields[k] ?? null) !== (initialFields[k] ?? null)) out[k] = fields[k]
    }
    return out
  }

  async function onSave() {
    const result = await save({
      claimId: claim.id,
      fieldsDiff: diffFields(),
      adjustments,
    })
    if (result.ok) onClose()
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-ink/20" onClick={saving ? undefined : onClose} />
      <aside className="relative w-[520px] max-w-full bg-white h-full shadow-xl overflow-y-auto flex flex-col">
        <header className="px-5 py-4 border-b flex items-center justify-between sticky top-0 bg-white">
          <h2 className="font-serif font-semibold text-ink text-[18px]">Edit claim {claim.claim_number || ''}</h2>
          <button className="text-muted text-[13px]" onClick={onClose} disabled={saving}>✕ Close</button>
        </header>

        <div className="flex-1 px-5 py-4 space-y-5 text-[12px]">
          <Section title="Identifiers">
            <Field label="Claim #"><Text value={fields.claim_number} onChange={v => set('claim_number', v)} /></Field>
            <Field label="Payer claim #"><Text value={fields.payer_claim_number} onChange={v => set('payer_claim_number', v)} /></Field>
          </Section>

          <Section title="Routing">
            <Field label="Payer name"><Text value={fields.payer_name} onChange={v => set('payer_name', v)} /></Field>
            <Field label="Payer ID"><Text value={fields.payer_id} onChange={v => set('payer_id', v)} /></Field>
            <Field label="Subscriber ID"><Text value={fields.subscriber_id} onChange={v => set('subscriber_id', v)} /></Field>
            <Field label="Group #"><Text value={fields.group_number} onChange={v => set('group_number', v)} /></Field>
            <Field label="Insurance order">
              <select className="input w-full py-1 text-[12px]"
                      value={fields.insurance_order || 'primary'}
                      onChange={(e) => set('insurance_order', e.target.value)}>
                {INSURANCE_ORDERS.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </Field>
          </Section>

          <Section title="Dates">
            <Field label="DOS from"><Date value={fields.date_of_service_from} onChange={v => set('date_of_service_from', v)} /></Field>
            <Field label="DOS to"><Date value={fields.date_of_service_to} onChange={v => set('date_of_service_to', v)} /></Field>
            <Field label="Check #"><Text value={fields.check_number} onChange={v => set('check_number', v)} /></Field>
            <Field label="Check date"><Date value={fields.check_date} onChange={v => set('check_date', v)} /></Field>
          </Section>

          <Section title="Provider">
            <Field label="Rendering name"><Text value={fields.rendering_provider_name} onChange={v => set('rendering_provider_name', v)} /></Field>
            <Field label="Rendering NPI"><Text value={fields.rendering_provider_npi} onChange={v => set('rendering_provider_npi', v)} /></Field>
          </Section>

          <Section title="Patient">
            <PatientPicker value={fields.patient_id} onChange={(v) => set('patient_id', v)} />
          </Section>

          <Section title="Status & Notes">
            <Field label="Status">
              <select className="input w-full py-1 text-[12px]"
                      value={fields.status || 'pending'}
                      onChange={(e) => set('status', e.target.value)}>
                {CLAIM_STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </Field>
            <Field label="Notes">
              <textarea
                className="input w-full py-1 text-[12px]"
                rows={3}
                value={fields.notes || ''}
                onChange={(e) => set('notes', e.target.value)}
              />
            </Field>
          </Section>

          <Section title="Money">
            <Field label="Billed"><MoneyInput value={fields.billed_amount} onChange={v => set('billed_amount', v)} /></Field>
            <Field label="Allowed"><MoneyInput value={fields.allowed_amount} onChange={v => set('allowed_amount', v)} /></Field>
            <Field label="Paid"><MoneyInput value={fields.paid_amount} onChange={v => set('paid_amount', v)} /></Field>
            <Field label="Patient resp"><MoneyInput value={fields.patient_responsibility} onChange={v => set('patient_responsibility', v)} /></Field>
            <Field label="Contractual adj"><MoneyInput value={fields.contractual_adjustment} onChange={v => set('contractual_adjustment', v)} /></Field>
            <Field label="Other adj"><MoneyInput value={fields.other_adjustment} onChange={v => set('other_adjustment', v)} /></Field>
            <div className="flex items-center justify-between pt-1">
              <span className="text-muted">Balance (computed) 🔒</span>
              <span className="font-mono">${computedBalance.toFixed(2)}</span>
            </div>
          </Section>

          <Section title="Claim adjustments">
            <AdjustmentList value={adjustments} onChange={setAdjustments} disabled={saving} />
          </Section>

          {error && (
            <div className="card bg-red-50 border border-red-200 p-3 text-[12px] text-danger">
              <div className="font-semibold">Save failed at step {error.completed + 1} of {error.total}</div>
              <div>{error.message}</div>
              <div className="mt-1">{error.completed} of {error.total} changes applied.</div>
              <div className="mt-2 flex gap-2">
                <button className="btn-secondary py-1 px-2 text-[11px]"
                        onClick={() => { reset(); onSave() }}>Retry</button>
                <button className="text-[11px] underline" onClick={reset}>Dismiss</button>
              </div>
            </div>
          )}
        </div>

        <footer className="px-5 py-3 border-t flex justify-end gap-2 sticky bottom-0 bg-white">
          <button className="btn-secondary text-[12px]" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn-primary text-[12px]" onClick={onSave} disabled={saving}>
            {saving ? (step ? `Saving ${step}…` : 'Saving…') : 'Save'}
          </button>
        </footer>
      </aside>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div>
      <h3 className="text-[11px] uppercase tracking-wide text-muted mb-1">{title}</h3>
      <div className="space-y-2">{children}</div>
    </div>
  )
}
function Field({ label, children }) {
  return (
    <label className="block">
      <div className="text-[11px] text-muted mb-0.5">{label}</div>
      {children}
    </label>
  )
}
function Text({ value, onChange }) {
  return (
    <input className="input w-full py-1 text-[12px]" value={value ?? ''}
           onChange={(e) => onChange(e.target.value)} />
  )
}
function Date({ value, onChange }) {
  return (
    <input type="date" className="input w-full py-1 text-[12px]" value={value ?? ''}
           onChange={(e) => onChange(e.target.value || null)} />
  )
}
```

- [ ] **Step 3: Smoke-verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -10
```
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/hooks/useClaimEdit.js frontend/src/components/EditClaimDrawer.jsx
git commit -m "feat(frontend): EditClaimDrawer + useClaimEdit hook (sequential save)"
```

---

## Task 10: Frontend — `useServiceLineEdit` hook + `EditServiceLineDrawer` component

**Files:**
- Create: `frontend/src/hooks/useServiceLineEdit.js`
- Create: `frontend/src/components/EditServiceLineDrawer.jsx`

- [ ] **Step 1: Create the hook**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/hooks/useServiceLineEdit.js`:

```js
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Orchestrates the save sequence for a service-line drawer.
 *
 * save({ claimId, lineId, fields, adjustments }):
 *  - If lineId is null, POST /claims/{claimId}/service-lines with all fields,
 *    then POST each 'new' SL adjustment against the returned id.
 *  - Else PATCH /service-lines/{lineId} if fields changed, then
 *    POST/PATCH/DELETE SL adjustments in that order.
 *
 * del({ claimId, lineId }): DELETE /service-lines/{lineId}
 */
export function useServiceLineEdit() {
  const queryClient = useQueryClient()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [step, setStep] = useState(null)

  async function save({ claimId, lineId, fields, fieldsDiff, adjustments }) {
    setSaving(true); setError(null)
    try {
      if (lineId == null) {
        // Add mode — POST line, then POST its adjustments
        setStep('1/?')
        const r = await api.post(`/claims/${claimId}/service-lines`, fields || {})
        const newId = r.data.id
        const newAdj = adjustments.filter(a => a.op === 'new')
        for (let i = 0; i < newAdj.length; i++) {
          setStep(`${i + 2}/${newAdj.length + 1}`)
          await api.post(`/service-lines/${newId}/adjustments`, _adjBody(newAdj[i]))
        }
      } else {
        const ops = []
        if (fieldsDiff && Object.keys(fieldsDiff).length > 0) {
          ops.push({ kind: 'sl-patch', body: fieldsDiff })
        }
        for (const a of adjustments) {
          if (a.op === 'new') ops.push({ kind: 'sla-post', body: _adjBody(a) })
          else if (a.op === 'edited') ops.push({ kind: 'sla-patch', id: a.id, body: _adjBody(a) })
          else if (a.op === 'deleted') ops.push({ kind: 'sla-delete', id: a.id })
        }
        for (let i = 0; i < ops.length; i++) {
          setStep(`${i + 1}/${ops.length}`)
          const op = ops[i]
          if (op.kind === 'sl-patch') await api.patch(`/service-lines/${lineId}`, op.body)
          else if (op.kind === 'sla-post') await api.post(`/service-lines/${lineId}/adjustments`, op.body)
          else if (op.kind === 'sla-patch') await api.patch(`/service-line-adjustments/${op.id}`, op.body)
          else if (op.kind === 'sla-delete') await api.delete(`/service-line-adjustments/${op.id}`)
        }
      }
      setSaving(false); setStep(null)
      queryClient.invalidateQueries({ queryKey: ['claim', claimId] })
      return { ok: true }
    } catch (e) {
      setError({ message: e?.response?.data?.detail || e.message || 'Save failed' })
      setSaving(false)
      queryClient.invalidateQueries({ queryKey: ['claim', claimId] })
      return { ok: false }
    }
  }

  async function del({ claimId, lineId }) {
    setSaving(true); setError(null)
    try {
      await api.delete(`/service-lines/${lineId}`)
      setSaving(false)
      queryClient.invalidateQueries({ queryKey: ['claim', claimId] })
      return { ok: true }
    } catch (e) {
      setError({ message: e?.response?.data?.detail || e.message || 'Delete failed' })
      setSaving(false)
      return { ok: false }
    }
  }

  function reset() { setError(null); setStep(null) }

  return { save, del, saving, error, step, reset }
}

function _adjBody(a) {
  const out = {
    group_code: a.group_code,
    reason_code: a.reason_code,
    amount: a.amount,
    reason_description: a.reason_description,
  }
  if (a.quantity !== undefined && a.quantity !== null && a.quantity !== '') {
    out.quantity = a.quantity
  }
  return out
}
```

- [ ] **Step 2: Create the drawer component**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/components/EditServiceLineDrawer.jsx`:

```jsx
import { useEffect, useMemo, useState } from 'react'
import MoneyInput from './MoneyInput'
import AdjustmentList from './AdjustmentList'
import { useServiceLineEdit } from '../hooks/useServiceLineEdit'

const EDITABLE_SL_FIELDS = [
  'procedure_code', 'modifier_1', 'modifier_2', 'modifier_3', 'modifier_4',
  'revenue_code', 'description', 'units',
  'date_of_service_from', 'date_of_service_to',
  'billed_amount', 'allowed_amount', 'paid_amount',
  'patient_responsibility', 'contractual_adjustment', 'other_adjustment',
  'diagnosis_codes',
]


export default function EditServiceLineDrawer({ claimId, line, onClose }) {
  // line === null → add mode; otherwise edit mode
  const isAdd = line == null
  const initialFields = useMemo(() => {
    const o = {}
    for (const k of EDITABLE_SL_FIELDS) o[k] = line?.[k] ?? (k === 'diagnosis_codes' ? [] : null)
    return o
  }, [line])

  const [fields, setFields] = useState(initialFields)
  const [adjustments, setAdjustments] = useState(
    (line?.adjustments || []).map(a => ({ ...a, op: 'none' }))
  )
  const [dxInput, setDxInput] = useState((initialFields.diagnosis_codes || []).join(', '))
  const [confirmDelete, setConfirmDelete] = useState(false)
  const { save, del, saving, error, step, reset } = useServiceLineEdit()

  useEffect(() => { document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = '' } }, [])

  function set(k, v) { setFields(prev => ({ ...prev, [k]: v })) }

  function diffFields() {
    const out = {}
    for (const k of EDITABLE_SL_FIELDS) {
      const cur = fields[k]
      const orig = initialFields[k]
      if (k === 'diagnosis_codes') {
        if (JSON.stringify(cur || []) !== JSON.stringify(orig || [])) out[k] = cur || []
      } else if ((cur ?? null) !== (orig ?? null)) {
        out[k] = cur
      }
    }
    return out
  }

  function commitDxInput() {
    const codes = dxInput.split(',').map(s => s.trim()).filter(Boolean)
    set('diagnosis_codes', codes)
  }

  async function onSave() {
    commitDxInput()
    // Re-read fields with dx committed (setState is async — read via diffFields next tick)
    // Safer: compute the outgoing body inline
    const codes = dxInput.split(',').map(s => s.trim()).filter(Boolean)
    const outFields = { ...fields, diagnosis_codes: codes }
    const diffWithDx = { ...diffFields() }
    if (JSON.stringify(codes) !== JSON.stringify(initialFields.diagnosis_codes || [])) {
      diffWithDx.diagnosis_codes = codes
    }

    const result = await save({
      claimId,
      lineId: isAdd ? null : line.id,
      fields: isAdd ? _sanitizeForPost(outFields) : undefined,
      fieldsDiff: isAdd ? undefined : diffWithDx,
      adjustments,
    })
    if (result.ok) onClose()
  }

  async function onDelete() {
    const result = await del({ claimId, lineId: line.id })
    if (result.ok) onClose()
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-ink/20" onClick={saving ? undefined : onClose} />
      <aside className="relative w-[520px] max-w-full bg-white h-full shadow-xl overflow-y-auto flex flex-col">
        <header className="px-5 py-4 border-b flex items-center justify-between sticky top-0 bg-white">
          <h2 className="font-serif font-semibold text-ink text-[18px]">
            {isAdd ? 'Add service line' : `Edit line ${line.procedure_code || ''}`}
          </h2>
          <button className="text-muted text-[13px]" onClick={onClose} disabled={saving}>✕ Close</button>
        </header>

        <div className="flex-1 px-5 py-4 space-y-5 text-[12px]">
          <Section title="Code">
            <Field label="Procedure code"><Text value={fields.procedure_code} onChange={v => set('procedure_code', v)} /></Field>
            <Field label="Revenue code"><Text value={fields.revenue_code} onChange={v => set('revenue_code', v)} /></Field>
            <Field label="Description"><Text value={fields.description} onChange={v => set('description', v)} /></Field>
          </Section>

          <Section title="Modifiers">
            <div className="grid grid-cols-4 gap-2">
              {['modifier_1','modifier_2','modifier_3','modifier_4'].map(k => (
                <input key={k} className="input py-1 text-[12px] font-mono"
                       placeholder={k.replace('modifier_','M')}
                       value={fields[k] || ''}
                       onChange={e => set(k, e.target.value)} />
              ))}
            </div>
          </Section>

          <Section title="Dates">
            <Field label="DOS from"><Date value={fields.date_of_service_from} onChange={v => set('date_of_service_from', v)} /></Field>
            <Field label="DOS to"><Date value={fields.date_of_service_to} onChange={v => set('date_of_service_to', v)} /></Field>
          </Section>

          <Section title="Quantity">
            <Field label="Units">
              <input type="number" step="0.01" className="input w-28 py-1 text-[12px]"
                     value={fields.units ?? ''}
                     onChange={e => set('units', e.target.value)} />
            </Field>
          </Section>

          <Section title="Diagnosis codes">
            <input className="input w-full py-1 text-[12px] font-mono"
                   placeholder="Z00.00, E11.9, ..."
                   value={dxInput}
                   onChange={e => setDxInput(e.target.value)}
                   onBlur={commitDxInput} />
            <div className="text-[11px] text-muted mt-1">Comma-separated ICD-10 codes.</div>
          </Section>

          <Section title="Money">
            <Field label="Billed"><MoneyInput value={fields.billed_amount} onChange={v => set('billed_amount', v)} /></Field>
            <Field label="Allowed"><MoneyInput value={fields.allowed_amount} onChange={v => set('allowed_amount', v)} /></Field>
            <Field label="Paid"><MoneyInput value={fields.paid_amount} onChange={v => set('paid_amount', v)} /></Field>
            <Field label="Patient resp"><MoneyInput value={fields.patient_responsibility} onChange={v => set('patient_responsibility', v)} /></Field>
            <Field label="Contractual adj"><MoneyInput value={fields.contractual_adjustment} onChange={v => set('contractual_adjustment', v)} /></Field>
            <Field label="Other adj"><MoneyInput value={fields.other_adjustment} onChange={v => set('other_adjustment', v)} /></Field>
          </Section>

          <Section title="Line adjustments">
            <AdjustmentList value={adjustments} onChange={setAdjustments} disabled={saving} />
          </Section>

          {error && (
            <div className="card bg-red-50 border border-red-200 p-3 text-[12px] text-danger">
              <div className="font-semibold">Save failed</div>
              <div>{error.message}</div>
              <div className="mt-2 flex gap-2">
                <button className="btn-secondary py-1 px-2 text-[11px]"
                        onClick={() => { reset(); onSave() }}>Retry</button>
                <button className="text-[11px] underline" onClick={reset}>Dismiss</button>
              </div>
            </div>
          )}
        </div>

        <footer className="px-5 py-3 border-t flex justify-between items-center sticky bottom-0 bg-white">
          <div>
            {!isAdd && (
              confirmDelete ? (
                <div className="flex items-center gap-2 text-[11px]">
                  <span className="text-danger">Delete this line?</span>
                  <button className="btn-secondary py-1 px-2 text-[11px] text-danger"
                          onClick={onDelete} disabled={saving}>Yes, delete</button>
                  <button className="text-[11px] underline"
                          onClick={() => setConfirmDelete(false)} disabled={saving}>cancel</button>
                </div>
              ) : (
                <button className="text-[11px] text-danger underline"
                        onClick={() => setConfirmDelete(true)} disabled={saving}>Delete line</button>
              )
            )}
          </div>
          <div className="flex gap-2">
            <button className="btn-secondary text-[12px]" onClick={onClose} disabled={saving}>Cancel</button>
            <button className="btn-primary text-[12px]" onClick={onSave} disabled={saving}>
              {saving ? (step ? `Saving ${step}…` : 'Saving…') : 'Save'}
            </button>
          </div>
        </footer>
      </aside>
    </div>
  )
}

function _sanitizeForPost(fields) {
  // Drop null/empty fields from the POST body to avoid sending "" to numeric columns
  const out = {}
  for (const [k, v] of Object.entries(fields)) {
    if (v === null || v === undefined || v === '') continue
    if (Array.isArray(v) && v.length === 0) continue
    out[k] = v
  }
  return out
}

function Section({ title, children }) {
  return (
    <div>
      <h3 className="text-[11px] uppercase tracking-wide text-muted mb-1">{title}</h3>
      <div className="space-y-2">{children}</div>
    </div>
  )
}
function Field({ label, children }) {
  return (
    <label className="block">
      <div className="text-[11px] text-muted mb-0.5">{label}</div>
      {children}
    </label>
  )
}
function Text({ value, onChange }) {
  return (
    <input className="input w-full py-1 text-[12px]" value={value ?? ''}
           onChange={(e) => onChange(e.target.value)} />
  )
}
function Date({ value, onChange }) {
  return (
    <input type="date" className="input w-full py-1 text-[12px]" value={value ?? ''}
           onChange={(e) => onChange(e.target.value || null)} />
  )
}
```

- [ ] **Step 3: Smoke-verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -10
```
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/hooks/useServiceLineEdit.js frontend/src/components/EditServiceLineDrawer.jsx
git commit -m "feat(frontend): EditServiceLineDrawer + useServiceLineEdit (add/edit/delete)"
```

---

## Task 11: Frontend — wire drawers into `ClaimDetail.jsx` + verification

**Files:**
- Modify: `frontend/src/pages/ClaimDetail.jsx`

- [ ] **Step 1: Update `ClaimDetail.jsx`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/ClaimDetail.jsx`.

Add imports near the top (after the existing imports):

```js
import { Pencil, Plus } from 'lucide-react'
import EditClaimDrawer from '../components/EditClaimDrawer'
import EditServiceLineDrawer from '../components/EditServiceLineDrawer'
```

Add two new state hooks at the top of the component, after the existing `useState` calls (around line 11):

```js
  const [editingClaim, setEditingClaim] = useState(false)
  const [editingLine, setEditingLine] = useState(null)  // null=closed, 'add'=add mode, <line object>=edit
```

In the header `<div className="ml-auto flex gap-2">` block (around line 44), add the Edit claim button between the status badge and the EOB button:

```jsx
<button className="btn-primary text-xs" onClick={() => setEditingClaim(true)}>
  <Pencil size={14} className="inline mr-1" />Edit claim
</button>
```

Update the Service Lines card (around line 104) — add the "+ Add line" button in the section header, and a per-row Edit button. Replace the existing `{claim.service_lines?.length > 0 && (...)}` block with:

```jsx
<div className="card mb-4">
  <div className="flex items-center justify-between mb-3">
    <h2 className="text-sm font-semibold text-gray-700">Service Lines</h2>
    <button className="btn-secondary text-xs" onClick={() => setEditingLine('add')}>
      <Plus size={14} className="inline mr-1" />Add line
    </button>
  </div>
  {claim.service_lines?.length > 0 ? (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-xs text-gray-500 uppercase">
            <th className="pb-2 text-left">CPT/Code</th>
            <th className="pb-2 text-left">Modifiers</th>
            <th className="pb-2 text-left">DOS</th>
            <th className="pb-2 text-right">Units</th>
            <th className="pb-2 text-right">Billed</th>
            <th className="pb-2 text-right">Paid</th>
            <th className="pb-2 text-right">Pt. Resp</th>
            <th className="pb-2 text-right"></th>
          </tr>
        </thead>
        <tbody>
          {claim.service_lines.map(svc => (
            <tr key={svc.id} className="border-b border-gray-50">
              <td className="py-2 font-mono font-medium">{svc.procedure_code}</td>
              <td className="py-2 text-gray-500 text-xs">
                {[svc.modifier_1, svc.modifier_2, svc.modifier_3, svc.modifier_4].filter(Boolean).join(' ')}
              </td>
              <td className="py-2 text-xs">{fmt.date(svc.date_of_service_from)}</td>
              <td className="py-2 text-right">{svc.units}</td>
              <td className="py-2 text-right font-mono">{fmt.currency(svc.billed_amount)}</td>
              <td className="py-2 text-right font-mono text-green-700">{fmt.currency(svc.paid_amount)}</td>
              <td className="py-2 text-right font-mono text-orange-600">{fmt.currency(svc.patient_responsibility)}</td>
              <td className="py-2 text-right">
                <button className="text-xs text-plum-600 underline" onClick={() => setEditingLine(svc)}>
                  ✎ Edit
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <div className="text-xs text-gray-500 italic">No service lines yet.</div>
  )}
</div>
```

Just before the closing `</div>` of the page (end of return statement, after the Appeal Result block), add the drawer mounts:

```jsx
{editingClaim && (
  <EditClaimDrawer claim={claim} onClose={() => setEditingClaim(false)} />
)}
{editingLine && (
  <EditServiceLineDrawer
    claimId={claim.id}
    line={editingLine === 'add' ? null : editingLine}
    onClose={() => setEditingLine(null)}
  />
)}
```

- [ ] **Step 2: Smoke-verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -10
```
Expected: build succeeds.

- [ ] **Step 3: Manual verification checklist**

Start the dev stack (backend + frontend):
```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && uvicorn app.main:app --reload &
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run dev &
```

Open `http://localhost:3000` and sign in as admin. Navigate to any existing claim detail page. Run through:

- [ ] Header shows a "✎ Edit claim" button next to the EOB PDF button.
- [ ] Click "Edit claim" → drawer slides in from right; current values populate all sections.
- [ ] Edit `notes`, Save → toast not required; drawer closes; detail view shows new notes.
- [ ] Edit `billed_amount` → drawer's live Balance preview recomputes. Save → detail view balance reflects new value.
- [ ] Open drawer again, "+ Add adjustment" → fill CO/45/$25/"Contractual", Save → row appears in detail if shown (or verify via `/api/claim-adjustments` or audit log).
- [ ] Edit an existing adjustment (pencil icon) → change amount → Save → persists.
- [ ] Delete an adjustment (✗) → undo link appears → click undo → row restored (still in local state). Delete again → Save → row gone.
- [ ] Service-lines card now has "+ Add line" button and each row has "✎ Edit" link.
- [ ] Click "+ Add line" → drawer opens empty → fill in procedure_code=99213, units=1, billed=100 → Save → new row appears in table.
- [ ] Click "✎ Edit" on a row → change modifiers → Save → row updates.
- [ ] In edit-line drawer, click "Delete line" → confirm → row disappears; any SL adjustments removed.
- [ ] Partial-failure: in browser devtools (Network tab) right-click a pending request and block its URL pattern, then trigger a Save with 3+ ops → red error banner shows "N of M applied · Retry" → remove the block, click Retry → sequence completes.
- [ ] Cancel button on a dirty drawer discards local edits (drawer closes, detail unchanged).
- [ ] Log out, log in as a clinical user (flip `ocooke@waldorfwomenscare.com`'s group via sqlite: `update users set "group"='clinical' where email='ocooke@waldorfwomenscare.com';`) — you should be redirected from `/claims` entirely (pre-existing BILLING guard); the editing UI is unreachable.
- [ ] Flip yourself back to admin.

- [ ] **Step 4: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/ClaimDetail.jsx
git commit -m "feat(frontend): wire Edit claim + Edit/Add service line drawers into ClaimDetail"
```

- [ ] **Step 5: Final test run**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ -v 2>&1 | tail -30
```
Expected: all backend tests pass (original + 41 new = original+41).

---

## Summary

Total new tests: **6** (claim_math) **+ 13** (claim edit) **+ 10** (service lines) **+ 9** (claim adjustments) **+ 9** (SL adjustments) = **47** new backend tests.

Total commits: **11** (one per task, all independently revertable).

Files created:
- Backend: 4 routers, 1 service, 5 test files = 10 files.
- Frontend: 3 shared components, 2 drawer components, 2 hooks = 7 files.

Files modified:
- `backend/app/routers/claims.py` (expand PATCH)
- `backend/app/main.py` (add 3 include_router calls)
- `frontend/src/pages/ClaimDetail.jsx` (wire drawers)
