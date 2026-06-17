"""Pellet payment pricing + insertion-credit ledger (Phase 2).
Stripe checkout/subscription creation + webhook handlers are added in
later tasks but live in this same module."""
from __future__ import annotations

import os
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session

from app.services.pellet.settings import cfg
from app.models.pellet_payment import PelletInsertionCredit, PelletSubscription, PelletPayment
from app.models.stripe_payment import StripeCustomer
from app.utils.dt import now_utc_naive


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
    label = "Pellet insertion" if count == 1 else f"Pellet insertions x{count}"
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
