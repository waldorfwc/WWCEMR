"""Stripe payments service.

Thin wrapper around the Stripe SDK with three responsibilities:

  1. Find-or-create a Stripe customer for one of our chart_numbers.
  2. Create a Checkout Session for a SurgeryPayment.
  3. Parse + verify a webhook event (returns the typed dict; the router
     decides what to do with it).

Soft-fail is NOT used here — booking endpoints in H3 must surface the
Stripe error to the coordinator/patient. The webhook handler does its own
defensive try/except.

Configuration:
  STRIPE_SECRET_KEY        sk_test_… or sk_live_…
  STRIPE_WEBHOOK_SECRET    whsec_…  (Stripe Dashboard → Webhooks → endpoint → signing secret)
  STRIPE_SUCCESS_URL       URL the patient is redirected to after a successful payment
                           (defaults to https://gw.waldorfwomenscare.com/p/payment/success)
  STRIPE_CANCEL_URL        URL after a cancelled payment
                           (defaults to https://gw.waldorfwomenscare.com/p/payment/cancelled)
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.stripe_payment import StripeCustomer, SurgeryPayment
from app.models.surgery import Surgery

log = logging.getLogger(__name__)


# ─── Configuration ──────────────────────────────────────────────────

def _stripe_key() -> str:
    return os.environ.get("STRIPE_SECRET_KEY", "").strip()


def _webhook_secret() -> str:
    return os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()


def _success_url() -> str:
    return os.environ.get(
        "STRIPE_SUCCESS_URL",
        "https://gw.waldorfwomenscare.com/p/payment/success",
    )


def _cancel_url() -> str:
    return os.environ.get(
        "STRIPE_CANCEL_URL",
        "https://gw.waldorfwomenscare.com/p/payment/cancelled",
    )


def is_configured() -> bool:
    return bool(_stripe_key())


def _client():
    """Lazy import + return the configured stripe module."""
    import stripe
    stripe.api_key = _stripe_key()
    return stripe


# ─── Customers ──────────────────────────────────────────────────────

def get_or_create_customer(db: Session, surgery: Surgery) -> str:
    """Return the Stripe customer ID for this surgery's chart_number,
    creating one if we haven't seen this chart_number before."""
    existing = (db.query(StripeCustomer)
                  .filter(StripeCustomer.chart_number == surgery.chart_number)
                  .first())
    if existing:
        return existing.stripe_customer_id

    s = _client()
    cust = s.Customer.create(
        email=surgery.email or None,
        name=surgery.patient_name,
        metadata={"chart_number": surgery.chart_number},
    )
    db.add(StripeCustomer(
        chart_number=surgery.chart_number,
        stripe_customer_id=cust.id,
        email=surgery.email,
        name=surgery.patient_name,
    ))
    db.commit()
    return cust.id


# ─── Checkout sessions ──────────────────────────────────────────────

def create_checkout_session(
    db: Session,
    surgery: Surgery,
    amount: Decimal,
    description: str,
    actor: str,
    *,
    kind: str = "patient_balance",
) -> SurgeryPayment:
    """Create a Stripe Checkout Session + a matching SurgeryPayment row.
    Returns the SurgeryPayment with .checkout_url populated."""
    if amount <= 0:
        raise ValueError("amount must be > 0")

    customer_id = get_or_create_customer(db, surgery)

    s = _client()
    amount_cents = int((amount * 100).quantize(Decimal("1")))
    session = s.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": amount_cents,
                "product_data": {
                    "name": description or "Surgery payment",
                    "description": f"Chart #{surgery.chart_number} — "
                                    f"{surgery.patient_name}",
                },
            },
            "quantity": 1,
        }],
        payment_intent_data={
            "metadata": {
                "surgery_id":   str(surgery.id),
                "chart_number": surgery.chart_number,
            },
        },
        metadata={
            "surgery_id":   str(surgery.id),
            "chart_number": surgery.chart_number,
        },
        success_url=_success_url(),
        cancel_url=_cancel_url(),
    )

    # Supersede any prior open requests for this surgery (a Stripe Checkout
    # Session expires after 24h anyway, and stacked 'requested' rows clutter
    # the payment history view).
    (db.query(SurgeryPayment)
       .filter(SurgeryPayment.surgery_id == surgery.id,
               SurgeryPayment.status == "requested")
       .update({"status": "expired"}, synchronize_session=False))

    pay = SurgeryPayment(
        surgery_id=surgery.id,
        stripe_checkout_session_id=session.id,
        stripe_customer_id=customer_id,
        amount_requested=amount,
        currency="usd",
        status="requested",
        kind=kind,
        description=description,
        requested_by=actor,
        checkout_url=session.url,
    )
    db.add(pay)
    db.commit(); db.refresh(pay)
    return pay


# ─── Receipts ───────────────────────────────────────────────────────

def get_receipt_url(payment: SurgeryPayment) -> Optional[str]:
    if not payment.stripe_payment_intent_id:
        return None
    try:
        s = _client()
        pi = s.PaymentIntent.retrieve(
            payment.stripe_payment_intent_id,
            expand=["latest_charge"],
        )
        charge = pi.get("latest_charge") or {}
        return charge.get("receipt_url")
    except Exception as exc:
        log.warning("get_receipt_url failed for %s: %s", payment.id, exc)
        return None


# ─── Refunds ────────────────────────────────────────────────────────

def refund_payment(
    db: Session,
    payment: SurgeryPayment,
    amount: Optional[Decimal] = None,
) -> dict:
    """Issue a full or partial refund. Webhook will follow up with
    `charge.refunded` to update local state — this returns the Stripe
    refund object id so the caller can include it in the audit row."""
    if payment.stripe_payment_intent_id is None:
        raise ValueError("payment has no stripe_payment_intent_id (never resolved)")

    s = _client()
    args = {"payment_intent": payment.stripe_payment_intent_id}
    if amount is not None:
        args["amount"] = int((amount * 100).quantize(Decimal("1")))
    return s.Refund.create(**args)


# ─── Webhook parsing ────────────────────────────────────────────────

def parse_webhook_event(payload: bytes, signature: str) -> dict:
    """Verify the Stripe signature and return the parsed event dict.
    Raises ValueError on bad signature."""
    secret = _webhook_secret()
    if not secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET is not configured")
    s = _client()
    try:
        event = s.Webhook.construct_event(payload, signature, secret)
    except Exception as e:
        raise ValueError(f"webhook signature verification failed: {e}")
    return event
