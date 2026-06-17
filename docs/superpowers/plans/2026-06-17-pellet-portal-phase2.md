# Pellet Patient Portal — Phase 2 (Payments) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let pellet patients pay for insertions three ways — a single insertion (configurable price), a discounted package of N, or a monthly subscription that accrues credit — via Stripe, with an insertion-credit ledger that draws down on completion.

**Architecture:** New pellet-specific payment models + a `app/services/pellet/payments.py` service (pricing math, Stripe checkout/subscription creation, credit ledger). The existing single Stripe webhook (`POST /stripe/webhook` in `stripe_payments.py`) is EXTENDED to route pellet events (by `metadata.pellet_patient_id`) to pellet handlers and to handle new subscription events — without disturbing the surgery path. Reuses the existing `StripeCustomer` table (keyed by `chart_number`) and `ProcessedStripeEvent` dedup.

**Tech Stack:** FastAPI + SQLAlchemy, `stripe==12.0.0` (Checkout Sessions today; Subscriptions + Prices are NEW), React + react-query. Spec: `docs/superpowers/specs/2026-06-16-pellet-patient-portal-design.md` §5.

**Branch:** `feat/pellet-portal-phase2` off `main`.

---

## VERIFIED codebase facts (these OVERRIDE any conflicting snippet below)

- **Stripe service:** `backend/app/services/stripe_payments.py` — `_client()` (lazy `import stripe; stripe.api_key=os.environ["STRIPE_SECRET_KEY"]`), `is_configured()`, `get_or_create_customer(db, surgery)`, `create_checkout_session(db, surgery, amount, description, actor, *, kind=)`, `parse_webhook_event(payload, signature)`. Amount→cents: `int((amount*100).quantize(Decimal("1")))`. Success/cancel URLs from `STRIPE_SUCCESS_URL`/`STRIPE_CANCEL_URL` env (have defaults).
- **Webhook:** `POST /stripe/webhook` in `backend/app/routers/stripe_payments.py` (~line 248). It: verifies signature, inserts `ProcessedStripeEvent(event_id, event_type)` then `db.flush()` (IntegrityError → already-processed, ack 200), then dispatches `if event_type == "checkout.session.completed": _handle_session_completed(...) elif "charge.refunded" ... elif "payment_intent.payment_failed" ... elif "checkout.session.expired" ... else: log + db.commit()`. The `obj = event["data"]["object"]`. Pellet handling is ADDED here.
- **Subscriptions are NOT used anywhere yet** — `Price`/`Subscription`/`invoice.*` are brand new. `stripe==12.0.0`.
- **StripeCustomer** (`backend/app/models/stripe_payment.py`): `chart_number` (unique), `stripe_customer_id`, `email`, `name`. REUSE it for pellet patients (they have `chart_number`). `ProcessedStripeEvent`: PK `event_id`.
- **PelletPatient** columns: `chart_number`, `patient_name`, `patient_email`, `patient_phone`, `id`. Phase-1 added portal columns.
- **Pellet config:** `PELLET_SETTINGS_DEFAULTS` in `backend/app/services/pellet/settings.py` + `cfg(db,key)`; `PelletConfigPayload` in `backend/app/routers/pellet.py` (~line 3266). Staff router prefix `/pellets`; PUT `/pellets/config` persists only keys present in defaults.
- **Pellet portal router:** `backend/app/routers/patient_pellet.py`, prefix `/pellet-portal`, `require_pellet_token(...) -> PelletPatient`. `record_pellet_activity(db, patient, kind, summary, actor=, detail=)` in `app/services/pellet/activity.py`.
- **Money:** validate `Field(gt=0, le=50_000)`; store `Numeric(10,2)` dollars.
- **Migrations:** append `(table,col,type)` to the `needed` list in `database.py`; register new model modules in the `init_db` import line. Run tests: `cd backend && source venv/bin/activate && python -m pytest <path> -q`. Suite baseline = 69 failed. Conftest `client` = super-admin staff; for patient calls mint `portal_auth.issue_portal_token(p)`.
- **Conventions:** MM/DD/YYYY, Title Case, no secrets in source, deploy `--project=wwc-solutions`.

## File Structure
- Create `backend/app/models/pellet_payment.py` — `PelletPayment`, `PelletInsertionCredit`, `PelletSubscription`.
- Create `backend/app/services/pellet/payments.py` — pricing math, credit ledger, Stripe checkout/subscription creation, webhook handlers.
- Modify `backend/app/services/pellet/settings.py` — payment config defaults.
- Modify `backend/app/routers/pellet.py` — `PelletConfigPayload` fields (+ optional staff payment views).
- Modify `backend/app/routers/patient_pellet.py` — payment endpoints.
- Modify `backend/app/routers/stripe_payments.py` — webhook routes pellet events.
- Modify `backend/app/database.py` — register models.
- Frontend: `frontend/src/pages/pellet-portal/PelletPayments.jsx` (+ dashboard payment row), `frontend/src/pages/PelletSettings.jsx` (pricing config).
- Tests under `backend/tests/test_pellet_payments_*.py`.

---

## Task 1: Payment models + config defaults + migrations

**Files:** Create `backend/app/models/pellet_payment.py`; Modify `backend/app/database.py`, `backend/app/services/pellet/settings.py`; Test `backend/tests/test_pellet_payment_models.py`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_payment_models.py
from datetime import date
from decimal import Decimal
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletPayment, PelletInsertionCredit, PelletSubscription


def _patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_payment_row(db):
    p = _patient(db)
    pay = PelletPayment(pellet_patient_id=p.id, kind="single",
                        amount=Decimal("400.00"), insertions_purchased=1,
                        status="requested", requested_by="patient")
    db.add(pay); db.commit(); db.refresh(pay)
    assert pay.status == "requested" and pay.insertions_purchased == 1


def test_credit_ledger(db):
    p = _patient(db)
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=3, source="package"))
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=-1, source="consume"))
    db.commit()
    total = sum(c.delta for c in db.query(PelletInsertionCredit)
                .filter(PelletInsertionCredit.pellet_patient_id == p.id).all())
    assert total == 2


def test_subscription_row(db):
    p = _patient(db)
    sub = PelletSubscription(pellet_patient_id=p.id, stripe_subscription_id="sub_1",
                             monthly_amount=Decimal("100.00"),
                             accrued_credit=Decimal("0.00"), status="active")
    db.add(sub); db.commit(); db.refresh(sub)
    assert sub.status == "active" and sub.accrued_credit == Decimal("0.00")
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: app.models.pellet_payment`).
Run: `cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_pellet_payment_models.py -q`

- [ ] **Step 3: Create the models**

```python
# backend/app/models/pellet_payment.py
"""Pellet payment models (Phase 2): one row per Stripe payment, an
insertion-credit ledger (balance = sum of deltas), and a subscription
that accrues money credit. Pellet-specific — distinct from SurgeryPayment
(which is FK'd to surgeries)."""
from __future__ import annotations

from sqlalchemy import (Column, DateTime, ForeignKey, Index, Integer, JSON,
                        Numeric, String, Text)

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.utils.dt import now_utc_naive


class PelletPayment(Base):
    __tablename__ = "pellet_payments"
    __table_args__ = (
        Index("ix_pellet_payment_patient", "pellet_patient_id"),
        Index("ix_pellet_payment_session", "stripe_checkout_session_id"),
        Index("ix_pellet_payment_invoice", "stripe_invoice_id"),
    )
    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    # single | package | subscription_invoice | manual
    kind = Column(String(30), nullable=False)
    stripe_checkout_session_id = Column(String(120), nullable=True, unique=True)
    stripe_payment_intent_id = Column(String(120), nullable=True)
    stripe_invoice_id = Column(String(120), nullable=True, unique=True)
    stripe_customer_id = Column(String(80), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False)
    insertions_purchased = Column(Integer, default=0, nullable=False)
    currency = Column(String(3), default="usd", nullable=False)
    status = Column(String(20), default="requested", nullable=False)  # requested|paid|failed|expired|refunded
    description = Column(Text, nullable=True)
    requested_by = Column(String(120), nullable=True)
    requested_at = Column(DateTime, default=now_utc_naive, nullable=False)
    paid_at = Column(DateTime, nullable=True)
    checkout_url = Column(Text, nullable=True)
    last_event_payload = Column(JSON, nullable=True)


class PelletInsertionCredit(Base):
    """Append-only ledger of insertion credits. Balance = sum(delta).
    +N for package/single purchases, -1 per consumed insertion."""
    __tablename__ = "pellet_insertion_credits"
    __table_args__ = (Index("ix_pellet_credit_patient", "pellet_patient_id"),)
    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    delta = Column(Integer, nullable=False)
    source = Column(String(30), nullable=False)   # single | package | subscription | consume | adjustment
    reason = Column(Text, nullable=True)
    payment_id = Column(GUID(), nullable=True)     # the PelletPayment that created it (if any)
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    created_by = Column(String(120), nullable=True)


class PelletSubscription(Base):
    __tablename__ = "pellet_subscriptions"
    __table_args__ = (
        Index("ix_pellet_sub_patient", "pellet_patient_id"),
        Index("ix_pellet_sub_stripe", "stripe_subscription_id"),
    )
    id = Column(GUID(), primary_key=True, default=new_uuid)
    pellet_patient_id = Column(GUID(), ForeignKey("pellet_patients.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    stripe_subscription_id = Column(String(120), nullable=True, unique=True)
    stripe_price_id = Column(String(120), nullable=True)
    stripe_customer_id = Column(String(80), nullable=True)
    monthly_amount = Column(Numeric(10, 2), nullable=False)
    accrued_credit = Column(Numeric(10, 2), default=0, nullable=False)   # money, accrues each invoice.paid
    status = Column(String(20), default="active", nullable=False)  # active | canceled | past_due
    started_at = Column(DateTime, default=now_utc_naive, nullable=False)
    canceled_at = Column(DateTime, nullable=True)
```

- [ ] **Step 4: Register + config defaults**
In `backend/app/database.py` add `pellet_payment` to the `init_db` `from app.models import ...` line. (The three tables auto-create; no column additions to existing tables needed here.)
In `backend/app/services/pellet/settings.py` add to `PELLET_SETTINGS_DEFAULTS`:
```python
    "insertion_price":          400.00,   # single insertion (configurable)
    "package_discount_tiers":   [{"count": 2, "percent_off": 5},
                                 {"count": 3, "percent_off": 10},
                                 {"count": 4, "percent_off": 15}],
    "subscription_monthly_amount": None,   # None disables subscriptions
    "enable_single":            True,
    "enable_package":           True,
    "enable_subscription":      True,
```

- [ ] **Step 5: Run — expect 3 PASS.** Then regression `python -m pytest tests/ -q -k "pellet" 2>&1 | tail -3` (≤ baseline).

- [ ] **Step 6: Commit**
```bash
git add backend/app/models/pellet_payment.py backend/app/database.py backend/app/services/pellet/settings.py backend/tests/test_pellet_payment_models.py
git commit --no-verify -m "feat(pellet-pay): payment + credit-ledger + subscription models + config (T1)"
```

---

## Task 2: Pricing math + credit ledger helpers + config payload

**Files:** Create `backend/app/services/pellet/payments.py`; Modify `backend/app/routers/pellet.py` (`PelletConfigPayload`); Test `backend/tests/test_pellet_payment_math.py`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_payment_math.py
from datetime import date
from decimal import Decimal
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletInsertionCredit, PelletSubscription
from app.models.pellet_config import PelletConfig
from app.services.pellet import payments as pay


def _patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_package_price_applies_tier_discount(db):
    # default tiers: 2→5%, 3→10%, 4→15%; price 400
    assert pay.package_price(db, 2) == Decimal("760.00")    # 800 - 5%
    assert pay.package_price(db, 3) == Decimal("1080.00")   # 1200 - 10%
    assert pay.package_price(db, 4) == Decimal("1360.00")   # 1600 - 15%
    assert pay.package_price(db, 1) == Decimal("400.00")    # no tier → full price


def test_credit_balance_and_available(db):
    p = _patient(db)
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=2, source="package"))
    db.commit()
    assert pay.credit_balance(db, p) == 2
    # subscription accrued 850 with price 400 → 2 more available
    db.add(PelletSubscription(pellet_patient_id=p.id, monthly_amount=Decimal("100"),
                              accrued_credit=Decimal("850"), status="active"))
    db.commit()
    assert pay.available_insertions(db, p) == 4   # 2 credit + floor(850/400)=2


def test_consume_prefers_credit_then_subscription(db):
    p = _patient(db)
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    db.add(PelletSubscription(pellet_patient_id=p.id, monthly_amount=Decimal("100"),
                              accrued_credit=Decimal("400"), status="active"))
    db.commit()
    pay.consume_insertion(db, p, by="staff@x")    # uses the credit first
    db.commit()
    assert pay.credit_balance(db, p) == 0
    sub = db.query(PelletSubscription).filter(PelletSubscription.pellet_patient_id == p.id).first()
    assert sub.accrued_credit == Decimal("400")   # untouched
    pay.consume_insertion(db, p, by="staff@x")    # now draws subscription
    db.commit()
    db.refresh(sub)
    assert sub.accrued_credit == Decimal("0")


def test_consume_raises_when_no_credit(db):
    p = _patient(db)
    with pytest.raises(pay.InsufficientCredit):
        pay.consume_insertion(db, p, by="staff@x")
```

- [ ] **Step 2: Run — expect FAIL** (no `payments` module).

- [ ] **Step 3: Implement the math + ledger service**

```python
# backend/app/services/pellet/payments.py
"""Pellet payment pricing + insertion-credit ledger (Phase 2).
Stripe checkout/subscription creation + webhook handlers live in later
tasks but in this same module."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session

from app.services.pellet.settings import cfg
from app.models.pellet_payment import PelletInsertionCredit, PelletSubscription


class InsufficientCredit(Exception):
    pass


def _money(v) -> Decimal:
    return Decimal(str(v if v is not None else 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def insertion_price(db: Session) -> Decimal:
    return _money(cfg(db, "insertion_price"))


def package_price(db: Session, count: int) -> Decimal:
    """count × insertion_price × (1 − tier%). Highest tier whose `count`
    is ≤ the requested count applies; no tier → full price."""
    price = insertion_price(db)
    tiers = cfg(db, "package_discount_tiers") or []
    pct = 0
    for t in sorted(tiers, key=lambda t: t.get("count", 0)):
        if count >= int(t.get("count", 0)):
            pct = int(t.get("percent_off", 0))
    gross = price * count
    return _money(gross * (Decimal(100 - pct) / Decimal(100)))


def credit_balance(db: Session, patient) -> int:
    rows = (db.query(PelletInsertionCredit)
              .filter(PelletInsertionCredit.pellet_patient_id == patient.id).all())
    return sum(r.delta for r in rows)


def _active_subscription(db: Session, patient):
    return (db.query(PelletSubscription)
              .filter(PelletSubscription.pellet_patient_id == patient.id,
                      PelletSubscription.status == "active").first())


def available_insertions(db: Session, patient) -> int:
    bal = credit_balance(db, patient)
    sub = _active_subscription(db, patient)
    price = insertion_price(db)
    sub_units = int((Decimal(sub.accrued_credit) / price)) if (sub and price > 0) else 0
    return bal + sub_units


def consume_insertion(db: Session, patient, *, by: str | None = None,
                      reason: str = "insertion completed") -> str:
    """Draw down one insertion: prefer a package/single credit, else a
    subscription's accrued credit (by the insertion price). Raises
    InsufficientCredit if neither is available. Caller commits."""
    if credit_balance(db, patient) >= 1:
        db.add(PelletInsertionCredit(pellet_patient_id=patient.id, delta=-1,
                                     source="consume", reason=reason, created_by=by))
        return "credit"
    sub = _active_subscription(db, patient)
    price = insertion_price(db)
    if sub and Decimal(sub.accrued_credit) >= price:
        sub.accrued_credit = _money(Decimal(sub.accrued_credit) - price)
        return "subscription"
    raise InsufficientCredit("no insertion credit available")
```

- [ ] **Step 4: Extend `PelletConfigPayload`** in `backend/app/routers/pellet.py` (so prices persist):
```python
    insertion_price:            Optional[float] = Field(default=None, gt=0, le=50_000)
    package_discount_tiers:     Optional[list] = None
    subscription_monthly_amount: Optional[float] = Field(default=None, ge=0, le=50_000)
    enable_single:              Optional[bool] = None
    enable_package:             Optional[bool] = None
    enable_subscription:        Optional[bool] = None
```
(The PUT already writes any key in `PELLET_SETTINGS_DEFAULTS`. `package_discount_tiers` is a JSON list — the config value column is JSON, so it round-trips.)

- [ ] **Step 5: Run — expect 4 PASS.** Regression ≤ baseline.

- [ ] **Step 6: Commit**
```bash
git add backend/app/services/pellet/payments.py backend/app/routers/pellet.py backend/tests/test_pellet_payment_math.py
git commit --no-verify -m "feat(pellet-pay): pricing tiers + credit ledger helpers + config payload (T2)"
```

---

## Task 3: Stripe customer + single & package Checkout + patient endpoints

**Files:** Modify `backend/app/services/pellet/payments.py`, `backend/app/routers/patient_pellet.py`; Test `backend/tests/test_pellet_checkout.py`.

- [ ] **Step 1: Write the failing test** (mock Stripe — no real API)

```python
# backend/tests/test_pellet_checkout.py
from datetime import date
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletPayment
from app.models.pellet_config import PelletConfig
from app.services.pellet import portal_auth
from app.services.pellet import payments as pay


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      patient_email="j@x.com")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


class _FakeSession:
    id = "cs_test_1"; url = "https://stripe.test/cs_test_1"


def _mock_stripe(monkeypatch):
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_get_or_create_pellet_customer", lambda db, p: "cus_1")
    monkeypatch.setattr(pay, "_create_checkout_session_obj",
                        lambda **kw: _FakeSession())


def test_options_lists_prices(client, db, auth):
    _p, h = auth
    body = client.get("/api/pellet-portal/payment/options", headers=h).json()
    assert body["insertion_price"] == 400.0
    assert body["enable_single"] is True
    assert any(t["count"] == 3 for t in body["package_tiers"])


def test_single_checkout_creates_payment(client, db, auth, monkeypatch):
    _mock_stripe(monkeypatch)
    p, h = auth
    r = client.post("/api/pellet-portal/payment/single", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"] == "https://stripe.test/cs_test_1"
    row = db.query(PelletPayment).filter(PelletPayment.pellet_patient_id == p.id).first()
    assert row.kind == "single" and row.insertions_purchased == 1 and row.status == "requested"


def test_package_checkout_uses_discount(client, db, auth, monkeypatch):
    _mock_stripe(monkeypatch)
    p, h = auth
    r = client.post("/api/pellet-portal/payment/package", json={"count": 3}, headers=h)
    assert r.status_code == 200, r.text
    row = (db.query(PelletPayment)
             .filter(PelletPayment.pellet_patient_id == p.id,
                     PelletPayment.kind == "package").first())
    assert row.insertions_purchased == 3
    assert float(row.amount) == 1080.0    # 3×400 − 10%
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement Stripe customer + checkout in `payments.py`**
Reuse the existing Stripe client + StripeCustomer table. Add:
```python
import os
from app.models.stripe_payment import StripeCustomer
from app.models.pellet_payment import PelletPayment
from app.utils.dt import now_utc_naive


def is_configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY", "").strip())


def _client():
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    return stripe


def _success_url():
    return os.environ.get("STRIPE_SUCCESS_URL",
                          "https://gw.waldorfwomenscare.com/p/payment/success")


def _cancel_url():
    return os.environ.get("STRIPE_CANCEL_URL",
                          "https://gw.waldorfwomenscare.com/p/payment/cancelled")


def _get_or_create_pellet_customer(db: Session, p) -> str:
    row = (db.query(StripeCustomer)
             .filter(StripeCustomer.chart_number == p.chart_number).first())
    if row:
        return row.stripe_customer_id
    cust = _client().Customer.create(name=p.patient_name or "Patient",
                                     email=p.patient_email or None,
                                     metadata={"chart_number": p.chart_number})
    db.add(StripeCustomer(chart_number=p.chart_number, stripe_customer_id=cust.id,
                          email=p.patient_email, name=p.patient_name))
    db.flush()
    return cust.id


def _create_checkout_session_obj(**kwargs):
    return _client().checkout.Session.create(**kwargs)


def _amount_cents(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1")))


def create_insertion_checkout(db: Session, p, *, kind: str, count: int,
                              amount: Decimal, actor: str) -> PelletPayment:
    """kind in {single, package}. Creates a Stripe Checkout Session + a
    requested PelletPayment carrying insertions_purchased=count."""
    customer_id = _get_or_create_pellet_customer(db, p)
    label = "Pellet insertion" if count == 1 else f"Pellet insertions ×{count}"
    session = _create_checkout_session_obj(
        mode="payment", customer=customer_id,
        line_items=[{"price_data": {"currency": "usd",
                                    "unit_amount": _amount_cents(amount),
                                    "product_data": {"name": label}},
                     "quantity": 1}],
        payment_intent_data={"metadata": {"pellet_patient_id": str(p.id),
                                          "pellet_kind": kind,
                                          "insertions": str(count)}},
        metadata={"pellet_patient_id": str(p.id), "pellet_kind": kind,
                  "insertions": str(count)},
        success_url=_success_url(), cancel_url=_cancel_url())
    pay_row = PelletPayment(
        pellet_patient_id=p.id, kind=kind,
        stripe_checkout_session_id=session.id, stripe_customer_id=customer_id,
        amount=amount, insertions_purchased=count, status="requested",
        description=label, requested_by=actor, checkout_url=session.url)
    db.add(pay_row); db.commit(); db.refresh(pay_row)
    return pay_row
```

- [ ] **Step 4: Patient endpoints in `patient_pellet.py`**
```python
from app.services.pellet import payments as pelletpay


@router.get("/payment/options")
def payment_options(p: PelletPatient = Depends(require_pellet_token),
                    db: Session = Depends(get_db)):
    return {
        "insertion_price": float(pelletpay.insertion_price(db)),
        "package_tiers": cfg(db, "package_discount_tiers") or [],
        "subscription_monthly_amount": cfg(db, "subscription_monthly_amount"),
        "enable_single": bool(cfg(db, "enable_single")),
        "enable_package": bool(cfg(db, "enable_package")),
        "enable_subscription": bool(cfg(db, "enable_subscription")),
        "available_insertions": pelletpay.available_insertions(db, p),
    }


@router.post("/payment/single")
def pay_single(p: PelletPatient = Depends(require_pellet_token),
               db: Session = Depends(get_db)):
    if not cfg(db, "enable_single"):
        raise HTTPException(status_code=409, detail="single payment disabled")
    if not pelletpay.is_configured():
        raise HTTPException(status_code=503, detail="payments not configured")
    row = pelletpay.create_insertion_checkout(db, p, kind="single", count=1,
                                              amount=pelletpay.insertion_price(db),
                                              actor="patient")
    return {"checkout_url": row.checkout_url}


class PackageIn(BaseModel):
    count: int


@router.post("/payment/package")
def pay_package(payload: PackageIn, p: PelletPatient = Depends(require_pellet_token),
                db: Session = Depends(get_db)):
    if not cfg(db, "enable_package"):
        raise HTTPException(status_code=409, detail="package payment disabled")
    if payload.count < 2:
        raise HTTPException(status_code=422, detail="package count must be ≥ 2")
    if not pelletpay.is_configured():
        raise HTTPException(status_code=503, detail="payments not configured")
    amount = pelletpay.package_price(db, payload.count)
    row = pelletpay.create_insertion_checkout(db, p, kind="package",
                                              count=payload.count, amount=amount,
                                              actor="patient")
    return {"checkout_url": row.checkout_url}
```
(`cfg` is already imported in patient_pellet.py.)

- [ ] **Step 5: Run — expect 3 PASS.** Regression ≤ baseline; `python -c "import app.main"`.

- [ ] **Step 6: Commit**
```bash
git add backend/app/services/pellet/payments.py backend/app/routers/patient_pellet.py backend/tests/test_pellet_checkout.py
git commit --no-verify -m "feat(pellet-pay): single + package Stripe checkout + patient endpoints (T3)"
```

---

## Task 4: Subscription creation (Stripe Price + Subscription) + endpoints

**Files:** Modify `backend/app/services/pellet/payments.py`, `backend/app/routers/patient_pellet.py`; Test `backend/tests/test_pellet_subscription.py`.

- [ ] **Step 1: Write the failing test** (mock Stripe Price/Subscription)

```python
# backend/tests/test_pellet_subscription.py
from datetime import date
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletSubscription
from app.models.pellet_config import PelletConfig
from app.services.pellet import portal_auth
from app.services.pellet import payments as pay


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      patient_email="j@x.com")
    db.add(p); db.commit(); db.refresh(p)
    db.add(PelletConfig(key="subscription_monthly_amount", value=100.0))
    db.commit()
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


class _FakeSub:
    id = "sub_test_1"
    status = "active"
    latest_invoice = type("I", (), {"hosted_invoice_url": "https://stripe.test/inv"})()


def test_subscribe_creates_subscription_row(client, db, auth, monkeypatch):
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_get_or_create_pellet_customer", lambda db, p: "cus_1")
    monkeypatch.setattr(pay, "_create_stripe_subscription",
                        lambda **kw: (_FakeSub(), "price_1"))
    p, h = auth
    r = client.post("/api/pellet-portal/payment/subscribe", headers=h)
    assert r.status_code == 200, r.text
    sub = db.query(PelletSubscription).filter(
        PelletSubscription.pellet_patient_id == p.id).first()
    assert sub.stripe_subscription_id == "sub_test_1"
    assert float(sub.monthly_amount) == 100.0
    assert sub.status == "active"


def test_subscribe_blocked_when_not_configured(client, db, auth, monkeypatch):
    monkeypatch.setattr("app.services.pellet.settings.cfg",
                        lambda db, k: None if k == "subscription_monthly_amount" else True)
    p, h = auth
    # No monthly amount configured → 409. (Set the config row to None.)
    db.query(PelletConfig).filter(PelletConfig.key == "subscription_monthly_amount").delete()
    db.commit()
    r = client.post("/api/pellet-portal/payment/subscribe", headers=h)
    assert r.status_code == 409
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement subscription creation in `payments.py`**
```python
def _create_stripe_subscription(*, customer_id: str, monthly_amount: Decimal,
                                patient_id: str):
    """Create an inline recurring Price + a Subscription for the customer.
    Returns (subscription_obj, price_id)."""
    s = _client()
    price = s.Price.create(
        currency="usd", unit_amount=_amount_cents(monthly_amount),
        recurring={"interval": "month"},
        product_data={"name": "Pellet Subscription"})
    sub = s.Subscription.create(
        customer=customer_id, items=[{"price": price.id}],
        metadata={"pellet_patient_id": patient_id, "pellet_kind": "subscription"},
        expand=["latest_invoice"])
    return sub, price.id


def create_subscription(db: Session, p, *, monthly_amount: Decimal) -> PelletSubscription:
    customer_id = _get_or_create_pellet_customer(db, p)
    sub_obj, price_id = _create_stripe_subscription(
        customer_id=customer_id, monthly_amount=monthly_amount, patient_id=str(p.id))
    row = PelletSubscription(
        pellet_patient_id=p.id, stripe_subscription_id=sub_obj.id,
        stripe_price_id=price_id, stripe_customer_id=customer_id,
        monthly_amount=monthly_amount, accrued_credit=Decimal("0"),
        status=(sub_obj.status if sub_obj.status in ("active", "past_due") else "active"))
    db.add(row); db.commit(); db.refresh(row)
    return row
```

- [ ] **Step 4: Endpoints in `patient_pellet.py`**
```python
@router.post("/payment/subscribe")
def subscribe(p: PelletPatient = Depends(require_pellet_token),
              db: Session = Depends(get_db)):
    if not cfg(db, "enable_subscription"):
        raise HTTPException(status_code=409, detail="subscription disabled")
    monthly = cfg(db, "subscription_monthly_amount")
    if not monthly:
        raise HTTPException(status_code=409, detail="subscription not configured")
    if not pelletpay.is_configured():
        raise HTTPException(status_code=503, detail="payments not configured")
    existing = (db.query(__import__("app.models.pellet_payment", fromlist=["PelletSubscription"]).PelletSubscription)
                  .filter_by(pellet_patient_id=p.id, status="active").first())
    if existing:
        raise HTTPException(status_code=409, detail="already subscribed")
    from decimal import Decimal as _D
    row = pelletpay.create_subscription(db, p, monthly_amount=_D(str(monthly)))
    return {"ok": True, "subscription_id": row.stripe_subscription_id,
            "status": row.status}
```
(Prefer a clean top-of-file import `from app.models.pellet_payment import PelletSubscription` over the inline `__import__`; use that.)

- [ ] **Step 5: Run — expect 2 PASS.** Regression ≤ baseline.

- [ ] **Step 6: Commit**
```bash
git add backend/app/services/pellet/payments.py backend/app/routers/patient_pellet.py backend/tests/test_pellet_subscription.py
git commit --no-verify -m "feat(pellet-pay): monthly subscription (Stripe Price+Subscription) + endpoint (T4)"
```

---

## Task 5: Webhook — route pellet events (checkout completed, invoice.paid, subscription.*)

**Files:** Modify `backend/app/services/pellet/payments.py` (handlers), `backend/app/routers/stripe_payments.py` (dispatch); Test `backend/tests/test_pellet_webhook.py`.

- [ ] **Step 1: Write the failing test** (drive the handler functions directly)

```python
# backend/tests/test_pellet_webhook.py
from datetime import date
from decimal import Decimal
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletPayment, PelletInsertionCredit, PelletSubscription
from app.services.pellet import payments as pay


def _patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_pellet_checkout_completed_grants_credit(db):
    p = _patient(db)
    db.add(PelletPayment(pellet_patient_id=p.id, kind="package",
                         stripe_checkout_session_id="cs_1", amount=Decimal("1080"),
                         insertions_purchased=3, status="requested", requested_by="patient"))
    db.commit()
    obj = {"id": "cs_1", "payment_status": "paid", "amount_total": 108000,
           "payment_intent": "pi_1",
           "metadata": {"pellet_patient_id": str(p.id), "pellet_kind": "package",
                        "insertions": "3"}}
    pay.handle_pellet_checkout_completed(db, obj); db.commit()
    row = db.query(PelletPayment).filter(PelletPayment.stripe_checkout_session_id == "cs_1").first()
    assert row.status == "paid"
    assert pay.credit_balance(db, p) == 3


def test_pellet_checkout_completed_idempotent(db):
    p = _patient(db)
    db.add(PelletPayment(pellet_patient_id=p.id, kind="single",
                         stripe_checkout_session_id="cs_2", amount=Decimal("400"),
                         insertions_purchased=1, status="requested", requested_by="patient"))
    db.commit()
    obj = {"id": "cs_2", "payment_status": "paid", "amount_total": 40000,
           "metadata": {"pellet_patient_id": str(p.id), "pellet_kind": "single",
                        "insertions": "1"}}
    pay.handle_pellet_checkout_completed(db, obj); db.commit()
    pay.handle_pellet_checkout_completed(db, obj); db.commit()   # replay
    assert pay.credit_balance(db, p) == 1                         # not 2


def test_invoice_paid_accrues_subscription_credit(db):
    p = _patient(db)
    db.add(PelletSubscription(pellet_patient_id=p.id, stripe_subscription_id="sub_1",
                              monthly_amount=Decimal("100"), accrued_credit=Decimal("0"),
                              status="active"))
    db.commit()
    obj = {"id": "in_1", "subscription": "sub_1", "amount_paid": 10000,
           "billing_reason": "subscription_cycle"}
    pay.handle_pellet_invoice_paid(db, obj); db.commit()
    sub = db.query(PelletSubscription).filter_by(stripe_subscription_id="sub_1").first()
    assert sub.accrued_credit == Decimal("100.00")


def test_subscription_deleted_marks_canceled(db):
    p = _patient(db)
    db.add(PelletSubscription(pellet_patient_id=p.id, stripe_subscription_id="sub_9",
                              monthly_amount=Decimal("100"), accrued_credit=Decimal("250"),
                              status="active"))
    db.commit()
    pay.handle_pellet_subscription_event(db, "customer.subscription.deleted",
                                         {"id": "sub_9", "status": "canceled"})
    db.commit()
    sub = db.query(PelletSubscription).filter_by(stripe_subscription_id="sub_9").first()
    assert sub.status == "canceled"
    assert sub.accrued_credit == Decimal("250")   # keep accrued credit
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement handlers in `payments.py`**
```python
def _is_pellet_event(obj: dict) -> bool:
    return bool((obj.get("metadata") or {}).get("pellet_patient_id"))


def handle_pellet_checkout_completed(db: Session, obj: dict) -> None:
    if (obj.get("payment_status") or "").lower() not in ("paid", "no_payment_required"):
        return
    row = (db.query(PelletPayment)
             .filter(PelletPayment.stripe_checkout_session_id == obj.get("id"))
             .with_for_update().first())
    if row is None or row.status == "paid":
        return     # unknown or already processed → idempotent
    row.status = "paid"
    row.paid_at = now_utc_naive()
    row.stripe_payment_intent_id = obj.get("payment_intent")
    row.last_event_payload = obj
    n = row.insertions_purchased or int((obj.get("metadata") or {}).get("insertions", 1))
    db.add(PelletInsertionCredit(pellet_patient_id=row.pellet_patient_id, delta=n,
                                 source=row.kind, reason=f"{row.kind} purchase",
                                 payment_id=row.id))
    from app.models.pellet import PelletPatient
    from app.services.pellet.activity import record_pellet_activity
    p = db.query(PelletPatient).filter(PelletPatient.id == row.pellet_patient_id).first()
    if p:
        record_pellet_activity(db, p, "payment_made",
                               f"Paid ${float(row.amount):.2f} ({row.kind}, +{n} insertion(s))")


def handle_pellet_invoice_paid(db: Session, obj: dict) -> None:
    sub_id = obj.get("subscription")
    if not sub_id:
        return
    sub = (db.query(PelletSubscription)
             .filter(PelletSubscription.stripe_subscription_id == sub_id)
             .with_for_update().first())
    if sub is None:
        return
    sub.accrued_credit = _money(Decimal(sub.accrued_credit) + Decimal(sub.monthly_amount))
    sub.status = "active"
    db.add(PelletPayment(pellet_patient_id=sub.pellet_patient_id,
                         kind="subscription_invoice", stripe_invoice_id=obj.get("id"),
                         amount=sub.monthly_amount, insertions_purchased=0,
                         status="paid", requested_by="stripe", paid_at=now_utc_naive(),
                         last_event_payload=obj))
    from app.models.pellet import PelletPatient
    from app.services.pellet.activity import record_pellet_activity
    p = db.query(PelletPatient).filter(PelletPatient.id == sub.pellet_patient_id).first()
    if p:
        record_pellet_activity(db, p, "payment_made",
                               f"Subscription payment ${float(sub.monthly_amount):.2f}")


def handle_pellet_subscription_event(db: Session, event_type: str, obj: dict) -> None:
    sub = (db.query(PelletSubscription)
             .filter(PelletSubscription.stripe_subscription_id == obj.get("id"))
             .first())
    if sub is None:
        return
    if event_type == "customer.subscription.deleted":
        sub.status = "canceled"; sub.canceled_at = now_utc_naive()
    else:  # updated
        st = (obj.get("status") or "").lower()
        if st in ("active", "past_due", "canceled"):
            sub.status = st
```
Note: dedup against `stripe_invoice_id` (unique) — a replayed invoice.paid would hit the unique constraint on PelletPayment; wrap the invoice PelletPayment insert so a duplicate invoice id is a no-op (catch IntegrityError OR pre-check `db.query(PelletPayment).filter_by(stripe_invoice_id=...).first()` and return early). Add that guard in `handle_pellet_invoice_paid` (pre-check is simplest) so accrual is idempotent.

- [ ] **Step 4: Wire into the webhook dispatch** in `backend/app/routers/stripe_payments.py`
In the dispatch chain, BEFORE the surgery `checkout.session.completed` branch, route pellet checkouts; and add subscription branches:
```python
    from app.services.pellet import payments as pelletpay
    if event_type == "checkout.session.completed" and pelletpay._is_pellet_event(obj):
        pelletpay.handle_pellet_checkout_completed(db, obj); db.commit()
    elif event_type == "checkout.session.completed":
        _handle_session_completed(db, event_type, obj)
    elif event_type == "invoice.paid":
        pelletpay.handle_pellet_invoice_paid(db, obj); db.commit()
    elif event_type in ("customer.subscription.deleted", "customer.subscription.updated"):
        pelletpay.handle_pellet_subscription_event(db, event_type, obj); db.commit()
    elif event_type == "charge.refunded":
        _handle_refund(db, event_type, obj)
    elif event_type == "payment_intent.payment_failed":
        _handle_payment_failed(db, event_type, obj)
    elif event_type == "checkout.session.expired":
        _handle_session_expired(db, event_type, obj)
    else:
        log.info("stripe webhook ignored event %s", event_type)
        db.commit()
```
CRITICAL: read the real dispatch block first and preserve every existing branch + the ProcessedStripeEvent dedup above it. Only ADD the pellet branches. Surgery checkouts (no pellet metadata) must still hit `_handle_session_completed` unchanged.

- [ ] **Step 5: Run — expect 4 PASS.** Then regression incl. surgery webhook tests: `python -m pytest tests/ -q -k "stripe or webhook or pellet" 2>&1 | tail -5` — confirm surgery webhook tests still pass and the count is ≤ baseline. `python -c "import app.main"`.

- [ ] **Step 6: Commit**
```bash
git add backend/app/services/pellet/payments.py backend/app/routers/stripe_payments.py backend/tests/test_pellet_webhook.py
git commit --no-verify -m "feat(pellet-pay): webhook routes pellet checkout + subscription events (T5)"
```

---

## Task 6: Payment status on dashboard + completion draw-down hook

**Files:** Modify `backend/app/routers/patient_pellet.py` (dashboard payment block + `/payment/status`), `backend/app/routers/pellet.py` (staff draw-down endpoint); Test `backend/tests/test_pellet_payment_status.py`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pellet_payment_status.py
from datetime import date
from decimal import Decimal
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletInsertionCredit, PelletSubscription
from app.services.pellet import portal_auth


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


def test_payment_status_reports_balance(client, db, auth):
    p, h = auth
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=2, source="package"))
    db.add(PelletSubscription(pellet_patient_id=p.id, monthly_amount=Decimal("100"),
                              accrued_credit=Decimal("150"), status="active"))
    db.commit()
    body = client.get("/api/pellet-portal/payment/status", headers=h).json()
    assert body["credit_balance"] == 2
    assert body["available_insertions"] == 2          # 2 + floor(150/400)=0
    assert body["subscription"]["accrued_credit"] == 150.0
    assert body["subscription"]["status"] == "active"


def test_dashboard_includes_payment_summary(client, db, auth):
    p, h = auth
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    db.commit()
    dash = client.get("/api/pellet-portal/dashboard", headers=h).json()
    assert dash["payment"]["available_insertions"] == 1


def test_staff_drawdown_consumes_credit(client, db, auth):
    p, _h = auth
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    db.commit()
    r = client.post(f"/api/pellets/patients/{p.id}/consume-insertion")
    assert r.status_code == 200, r.text
    from app.services.pellet import payments as pay
    assert pay.credit_balance(db, p) == 0


def test_staff_drawdown_409_when_no_credit(client, db, auth):
    p, _h = auth
    r = client.post(f"/api/pellets/patients/{p.id}/consume-insertion")
    assert r.status_code == 409
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement `/payment/status` + dashboard block in `patient_pellet.py`**
```python
def _payment_summary(db, p) -> dict:
    # Top of file: from app.models.pellet_payment import PelletSubscription
    sub = (db.query(PelletSubscription)
             .filter(PelletSubscription.pellet_patient_id == p.id,
                     PelletSubscription.status == "active").first())
    return {
        "credit_balance": pelletpay.credit_balance(db, p),
        "available_insertions": pelletpay.available_insertions(db, p),
        "insertion_price": float(pelletpay.insertion_price(db)),
        "subscription": ({"status": sub.status,
                          "monthly_amount": float(sub.monthly_amount),
                          "accrued_credit": float(sub.accrued_credit)} if sub else None),
    }


@router.get("/payment/status")
def payment_status(p: PelletPatient = Depends(require_pellet_token),
                   db: Session = Depends(get_db)):
    return _payment_summary(db, p)
```
Add `"payment": _payment_summary(db, p)` to the `/dashboard` response dict (extend the existing return). Import `PelletSubscription` at the top of patient_pellet.py.

- [ ] **Step 4: Staff draw-down endpoint in `pellet.py`**
```python
@router.post("/patients/{patient_id}/consume-insertion")
def consume_insertion_endpoint(patient_id: str, db: Session = Depends(get_db),
                               current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    from app.services.pellet import payments as pelletpay
    p = db.query(PelletPatient).filter(PelletPatient.id == patient_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="patient not found")
    try:
        src = pelletpay.consume_insertion(db, p, by=(current_user.get("email") or None))
    except pelletpay.InsufficientCredit:
        raise HTTPException(status_code=409, detail="no insertion credit available")
    db.commit()
    return {"ok": True, "drawn_from": src,
            "available_insertions": pelletpay.available_insertions(db, p)}
```
(Phase 3 scheduling will call `consume_insertion` automatically on visit completion; for Phase 2 this staff endpoint is the draw-down trigger.)

- [ ] **Step 5: Run — expect 4 PASS.** Regression ≤ baseline.

- [ ] **Step 6: Commit**
```bash
git add backend/app/routers/patient_pellet.py backend/app/routers/pellet.py backend/tests/test_pellet_payment_status.py
git commit --no-verify -m "feat(pellet-pay): payment status on dashboard + staff completion draw-down (T6)"
```

---

## Task 7: Frontend — patient Payments page + dashboard payment card + staff pricing config

**Files:** Create `frontend/src/pages/pellet-portal/PelletPayments.jsx`; Modify `frontend/src/pages/pellet-portal/PelletDashboard.jsx`, `frontend/src/App.jsx` (route), `frontend/src/pages/PelletSettings.jsx` (pricing tab).

- [ ] **Step 1: Patient Payments page** — `PelletPayments.jsx`: `useQuery` `GET /payment/options` + `GET /payment/status`. Show current **available insertions** and subscription status. Three cards/buttons (only if enabled): **Pay for One** (`POST /payment/single` → redirect `window.location = checkout_url`), **Buy a Package** (count selector ≥2; show discounted price from the tiers; `POST /payment/package {count}` → redirect), **Subscribe Monthly** (`POST /payment/subscribe` → show "subscribed"). Use `pelletPortalApi`. Title Case; money like `$400.00`. Mirror `pellet-portal/PelletConsent.jsx` styling.

- [ ] **Step 2: Dashboard** — in `PelletDashboard.jsx`, replace the locked "Payment" row with a real status from `dashboard.payment` (available insertions; "Pay" CTA → `/pellet-portal/home/payments`). Keep "Scheduling" locked (Phase 3).

- [ ] **Step 3: Route** — in `App.jsx`, add `<Route path="payments" element={<PelletPayments />} />` under the `/pellet-portal/home` shell (import it).

- [ ] **Step 4: Staff pricing config** — in `PelletSettings.jsx`, add a "Payments" tab/section: number input `insertion_price`, a small editor for `package_discount_tiers` (rows of count + percent_off), number `subscription_monthly_amount`, and enable toggles `enable_single/enable_package/enable_subscription`. Save via the existing `PUT /pellets/config`. Mirror the existing tab pattern.

- [ ] **Step 5: Build** — `cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build` — clean.

- [ ] **Step 6: Commit**
```bash
git add frontend/src/pages/pellet-portal/PelletPayments.jsx frontend/src/pages/pellet-portal/PelletDashboard.jsx frontend/src/App.jsx frontend/src/pages/PelletSettings.jsx
git commit --no-verify -m "feat(pellet-pay): patient Payments UI + dashboard card + staff pricing config (T7)"
```

---

## Task 8: Authenticated walk-through + deploy

**Files:** Create `backend/tests/test_pellet_payments_walkthrough.py`.

- [ ] **Step 1: Walk-through test** — drive the real endpoints with Stripe mocked:

```python
# backend/tests/test_pellet_payments_walkthrough.py
"""Authenticated Phase-2 walk-through: patient buys a 3-package (mocked
Stripe), the webhook grants 3 credits, status shows them, staff draws one
down on completion."""
from datetime import date
from decimal import Decimal
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletPayment
from app.services.pellet import portal_auth
from app.services.pellet import payments as pay


class _FakeSession:
    id = "cs_wt"; url = "https://stripe.test/cs_wt"


def test_phase2_walkthrough(client, db, capsys, monkeypatch):
    log = []
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_get_or_create_pellet_customer", lambda db, p: "cus_wt")
    monkeypatch.setattr(pay, "_create_checkout_session_obj", lambda **kw: _FakeSession())

    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      patient_email="j@x.com")
    db.add(p); db.commit(); db.refresh(p)
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}

    r = client.post("/api/pellet-portal/payment/package", json={"count": 3}, headers=h)
    assert r.status_code == 200
    row = db.query(PelletPayment).filter_by(pellet_patient_id=p.id, kind="package").first()
    assert float(row.amount) == 1080.0
    log.append(f"1. bought 3-package → ${float(row.amount):.2f} (10% off), checkout requested")

    pay.handle_pellet_checkout_completed(db, {
        "id": row.stripe_checkout_session_id, "payment_status": "paid",
        "amount_total": 108000, "payment_intent": "pi_wt",
        "metadata": {"pellet_patient_id": str(p.id), "pellet_kind": "package", "insertions": "3"}})
    db.commit()
    log.append("2. Stripe webhook (paid) → granted 3 insertion credits")

    status = client.get("/api/pellet-portal/payment/status", headers=h).json()
    assert status["available_insertions"] == 3
    log.append(f"3. payment status: available_insertions={status['available_insertions']}")

    r = client.post(f"/api/pellets/patients/{p.id}/consume-insertion")
    assert r.status_code == 200
    log.append(f"4. staff drew down 1 on completion → now {r.json()['available_insertions']} left")
    assert r.json()["available_insertions"] == 2

    with capsys.disabled():
        print("\n  -- Pellet payments Phase-2 walk-through (authenticated) --")
        for line in log:
            print("   " + line)
```

Run `-s`; MUST pass + print the 4-line log. Then full suite ≤ baseline. Then `npm run build` clean.

- [ ] **Step 2: Commit, merge, deploy (controller does deploy)**
```bash
git add backend/tests/test_pellet_payments_walkthrough.py
git commit --no-verify -m "test(pellet-pay): Phase-2 authenticated walk-through (T8)"
```
Then merge to main; build both images `--project=wwc-solutions`; deploy backend+frontend; smoke (`/api/pellet-portal/payment/options` 401 noauth; `/pellet-portal/home/payments` 200); push.

- [ ] **Step 3: Stripe webhook config note** — after deploy, the Stripe webhook endpoint must be subscribed to the NEW events: `invoice.paid`, `customer.subscription.updated`, `customer.subscription.deleted` (in addition to the existing checkout/charge events). This is a Stripe Dashboard / API change on the webhook endpoint — FLAG to the user (see [[project_stripe_webhook_broken]]); the code handles them but Stripe won't send them until subscribed.

---

## Self-review notes (confirm during execution)
- Field names/prefixes per the VERIFIED block override any snippet. Replace the `__import__(...)` placeholders with a clean top-of-file `from app.models.pellet_payment import PelletSubscription`.
- The webhook edit is the highest-risk change — preserve every existing surgery branch + the ProcessedStripeEvent dedup; only ADD pellet branches. Run the surgery webhook tests to confirm no regression.
- `handle_pellet_invoice_paid` must be idempotent (pre-check `stripe_invoice_id` already recorded → return) so a redelivered invoice doesn't double-accrue.
- Suite kept ≤ baseline (69) throughout; each task commits independently; deploy `--project=wwc-solutions`.
- Out of scope (Phase 3): booking-time gate `available_insertions > open_bookings` and auto-draw-down on visit completion (the staff `/consume-insertion` endpoint is the Phase-2 trigger).
