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
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.larc import LarcAssignment
from app.models.larc_payment import LarcPayment
from app.models.stripe_payment import StripeCustomer, SurgeryPayment
from app.models.surgery import Surgery
from app.utils.dt import now_utc_naive

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
    creating one if we haven't seen this chart_number before.

    Concurrency: two first-payments for the same chart used to mint
    two Stripe customer rows. uq_stripe_customer_chart UniqueConstraint
    catches the duplicate at commit, so the second caller raises
    IntegrityError — handled here by reloading and using the row the
    other request just committed. (Fable billing audit L2.)
    """
    existing = (db.query(StripeCustomer)
                  .filter(StripeCustomer.chart_number == surgery.chart_number)
                  .first())
    if existing:
        return existing.stripe_customer_id

    s = _client()
    # Send only what Stripe needs for receipts (email + display name).
    # chart_number used to go in metadata for our own reconciliation
    # but we already store it on StripeCustomer locally, so there's
    # no reason to copy a chart identifier to a non-BAA processor.
    # (Fable billing audit M4.)
    cust = s.Customer.create(
        email=surgery.email or None,
        name=surgery.patient_name,
    )
    db.add(StripeCustomer(
        chart_number=surgery.chart_number,
        stripe_customer_id=cust.id,
        email=surgery.email,
        name=surgery.patient_name,
    ))
    from sqlalchemy.exc import IntegrityError
    try:
        db.commit()
        return cust.id
    except IntegrityError:
        # Another request committed the same chart_number first.
        # Reload the winning row and discard our just-created Stripe
        # Customer — log it loudly so an operator can clean up the
        # orphan in the Stripe dashboard (Stripe billing won't break
        # but it clutters reporting).
        db.rollback()
        log.warning("StripeCustomer race for chart %s — orphan Stripe "
                    "customer %s created; using winning row",
                    surgery.chart_number, cust.id)
        existing = (db.query(StripeCustomer)
                      .filter(StripeCustomer.chart_number == surgery.chart_number)
                      .first())
        return existing.stripe_customer_id if existing else cust.id


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
    # Line item description used to embed patient_name + chart_number,
    # which Stripe stores in cleartext on the line item AND emails to
    # the customer in the receipt. Stripe doesn't sign a BAA for
    # standard accounts. Switched to an opaque per-payment reference;
    # patient identity stays in Stripe's customer object (name + email)
    # which lives behind the BAA-eligible regulated-payment fields and
    # in our metadata.surgery_id (UUID). (Fable billing audit M4.)
    safe_description = description or "Surgery payment"
    session = s.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": amount_cents,
                "product_data": {
                    "name": safe_description,
                    "description": f"Ref: surgery {str(surgery.id)[:8]}",
                },
            },
            "quantity": 1,
        }],
        payment_intent_data={
            "metadata": {
                "surgery_id": str(surgery.id),
            },
        },
        metadata={
            "surgery_id": str(surgery.id),
        },
        success_url=_success_url(),
        cancel_url=_cancel_url(),
    )

    # Supersede any prior open requests for this surgery. Previously we
    # only flipped the local row to 'expired' — the actual Stripe
    # Checkout session remained payable for up to 24h. Patient could
    # pay the prior emailed link AND the new link, producing a real
    # double charge that the webhook handler used to honor (the local
    # 'expired' guard didn't reject the prior session's
    # checkout.session.completed event). Now we call Session.expire()
    # in Stripe before flipping local state — best-effort, since a
    # session that's already completed or expired returns an error
    # which we swallow + log. (Fable billing audit H1.)
    stale = (db.query(SurgeryPayment)
                .filter(SurgeryPayment.surgery_id == surgery.id,
                        SurgeryPayment.status == "requested")
                .all())
    stripe_client = _client()
    for old in stale:
        if not old.stripe_checkout_session_id:
            continue
        try:
            stripe_client.checkout.Session.expire(old.stripe_checkout_session_id)
        except Exception as exc:
            # Already completed/expired/disabled in Stripe — fine; the
            # local 'expired' marker below still applies and the webhook
            # will sort out any settled session via the event dedup.
            log.info("Stripe Session.expire(%s) failed: %s",
                     old.stripe_checkout_session_id, exc)
        old.status = "expired"

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
    try:
        db.commit(); db.refresh(pay)
    except Exception as exc:
        # Orphan Stripe session: the API call succeeded but the local
        # row couldn't commit (DB blip, validation error, etc.). The
        # webhook receiver will see a checkout.session.completed event
        # with no SurgeryPayment to attach the payment to — money lands
        # in Stripe with nowhere local to record it. Try to expire the
        # session immediately; log loudly either way so an operator can
        # reconcile manually. (Fable billing audit L5.)
        log.error("ORPHAN STRIPE SESSION — local commit failed for surgery %s "
                  "after creating session %s: %s",
                  surgery.id, session.id, exc)
        try:
            stripe_client.checkout.Session.expire(session.id)
            log.warning("orphan session %s expired in Stripe", session.id)
        except Exception as expire_exc:
            log.error("could not expire orphan session %s: %s",
                      session.id, expire_exc)
        db.rollback()
        raise
    return pay


# ─── LARC patient-responsibility checkout ───────────────────────────

def create_larc_checkout(
    db: Session,
    assignment: LarcAssignment,
    amount: Decimal,
    *,
    actor: str = "patient",
) -> dict:
    """Create a Stripe Checkout Session + a matching LarcPayment row for a
    LARC device patient-responsibility charge.

    Mirrors `create_checkout_session` for surgeries but without the
    StripeCustomer indirection — LARC assignments are keyed by chart number,
    not by a persisted Stripe customer. Returns
    ``{"checkout_url": ..., "payment_id": ...}``.

    Idempotency: re-uses a "requested" LarcPayment created in the last
    15 minutes for the same assignment + amount (mirrors the surgery
    creator's window) so a double-click doesn't mint a second session.
    """
    if amount <= 0:
        raise ValueError("amount must be > 0")

    # 15-min idempotency window — reuse a recent open request for the same
    # assignment + amount rather than minting a duplicate Stripe session.
    cutoff = now_utc_naive() - timedelta(minutes=15)
    recent = (db.query(LarcPayment)
                .filter(LarcPayment.assignment_id == str(assignment.id),
                        LarcPayment.status == "requested",
                        LarcPayment.amount_requested == amount,
                        LarcPayment.requested_at >= cutoff,
                        LarcPayment.checkout_url.isnot(None))
                .order_by(LarcPayment.requested_at.desc())
                .first())
    if recent is not None:
        return {"checkout_url": recent.checkout_url, "payment_id": str(recent.id)}

    s = _client()
    amount_cents = int((amount * 100).quantize(Decimal("1")))
    session = s.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": amount_cents,
                "product_data": {
                    "name": "LARC device — patient responsibility",
                    "description": f"Ref: LARC {str(assignment.id)[:8]}",
                },
            },
            "quantity": 1,
        }],
        payment_intent_data={
            "metadata": {
                "larc_assignment_id": str(assignment.id),
                "kind": "larc_patient_responsibility",
            },
        },
        metadata={
            "larc_assignment_id": str(assignment.id),
            "kind": "larc_patient_responsibility",
        },
        success_url=_success_url(),
        cancel_url=_cancel_url(),
    )

    pay = LarcPayment(
        assignment_id=str(assignment.id),
        kind="larc_patient_responsibility",
        status="requested",
        amount_requested=amount,
        stripe_checkout_session_id=session.id,
        checkout_url=session.url,
    )
    db.add(pay)
    try:
        db.commit(); db.refresh(pay)
    except Exception as exc:
        # Orphan Stripe session — the API call succeeded but the local row
        # didn't commit. Best-effort expire + log loudly so an operator can
        # reconcile. (Mirrors create_checkout_session's L5 guard.)
        log.error("ORPHAN STRIPE SESSION — LARC commit failed for assignment "
                  "%s after creating session %s: %s",
                  assignment.id, session.id, exc)
        try:
            _client().checkout.Session.expire(session.id)
            log.warning("orphan LARC session %s expired in Stripe", session.id)
        except Exception as expire_exc:
            log.error("could not expire orphan LARC session %s: %s",
                      session.id, expire_exc)
        db.rollback()
        raise
    return {"checkout_url": session.url, "payment_id": str(pay.id)}


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
