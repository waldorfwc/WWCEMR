"""Stripe payment data model.

  StripeCustomer        — one row per (chart_number) tied to a Stripe customer ID.
                          A patient with 5 surgeries shares one Stripe customer.
  SurgeryPayment        — one row per payment attempt for a Surgery. Tracks the
                          Stripe PaymentIntent / Checkout Session, amount, and
                          status driven by webhook events.
  SurgeryPaymentHistory — append-only audit row per state transition.

Statuses:
  unpaid     — placeholder (no row exists yet)
  requested  — Checkout Session created, link sent, awaiting patient action
  paid       — Stripe webhook confirmed payment success
  refunded   — Stripe webhook confirmed refund (full or partial)
  failed     — Payment attempt failed (card decline, etc.)
  expired    — Session expired without a completed payment
"""
from __future__ import annotations

from datetime import datetime
from app.utils.dt import now_utc_naive

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, JSON, Numeric, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid


SURGERY_PAYMENT_STATUSES = (
    "requested", "paid", "refunded", "failed", "expired"
)


class StripeCustomer(Base):
    __tablename__ = "stripe_customers"
    __table_args__ = (
        UniqueConstraint("chart_number", name="uq_stripe_customer_chart"),
    )

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    chart_number  = Column(String(20), nullable=False, index=True)
    stripe_customer_id = Column(String(80), nullable=False, unique=True)
    email         = Column(String(200), nullable=True)
    name          = Column(String(200), nullable=True)
    created_at    = Column(DateTime, default=now_utc_naive, nullable=False)
    updated_at    = Column(DateTime, default=now_utc_naive,
                              onupdate=now_utc_naive, nullable=False)


class SurgeryPayment(Base):
    __tablename__ = "surgery_payments"
    __table_args__ = (
        Index("ix_surgery_payment_surgery", "surgery_id"),
        Index("ix_surgery_payment_intent", "stripe_payment_intent_id"),
        Index("ix_surgery_payment_session", "stripe_checkout_session_id"),
    )

    id                          = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id                  = Column(GUID(),
                                          ForeignKey("surgeries.id", ondelete="CASCADE"),
                                          nullable=False)
    # Stripe identifiers — checkout session is created up front; payment
    # intent is the durable id once the session resolves.
    stripe_checkout_session_id  = Column(String(120), nullable=True, unique=True)
    stripe_payment_intent_id    = Column(String(120), nullable=True, unique=True)
    stripe_customer_id          = Column(String(80),  nullable=True)
    # Discriminates payment purpose so the webhook can route correctly:
    # "patient_balance" bumps Surgery.amount_paid;
    # "fmla_fee" sets Surgery.fmla_fee_paid (never touches amount_paid).
    kind = Column(String(40), default="patient_balance", nullable=False)
    # Money — stored in dollars, not cents, to match the existing Surgery
    # money columns (Numeric(10,2)).
    amount_requested            = Column(Numeric(10, 2), nullable=False)
    amount_paid                 = Column(Numeric(10, 2), default=0, nullable=False)
    amount_refunded             = Column(Numeric(10, 2), default=0, nullable=False)
    currency                    = Column(String(3),  default="usd", nullable=False)
    status                      = Column(String(20), default="requested", nullable=False)
    # values: see SURGERY_PAYMENT_STATUSES
    description                 = Column(Text, nullable=True)
    # Patient-facing line item shown on the Stripe Checkout page.
    requested_by                = Column(String(120), nullable=False)
    requested_at                = Column(DateTime, default=now_utc_naive, nullable=False)
    paid_at                     = Column(DateTime, nullable=True)
    refunded_at                 = Column(DateTime, nullable=True)
    failed_at                   = Column(DateTime, nullable=True)
    failure_reason              = Column(Text, nullable=True)
    checkout_url                = Column(Text, nullable=True)
    # Last known Stripe object as a debugging breadcrumb.
    last_event_payload          = Column(JSON, nullable=True)


class SurgeryPaymentHistory(Base):
    """Append-only audit of every state change driven by a Stripe webhook
    (or admin action). Never delete from this table."""
    __tablename__ = "surgery_payment_history"
    __table_args__ = (
        Index("ix_surgery_payment_history_payment", "payment_id"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    payment_id   = Column(GUID(),
                            ForeignKey("surgery_payments.id", ondelete="CASCADE"),
                            nullable=False)
    at           = Column(DateTime, default=now_utc_naive, nullable=False)
    actor        = Column(String(120), nullable=False)
    # Stripe webhook events use 'stripe:webhook' as actor; admin actions use
    # the staff email.
    event_type   = Column(String(60), nullable=False)
    # Examples: checkout.session.completed | payment_intent.succeeded |
    # charge.refunded | payment_intent.payment_failed |
    # admin.refund_initiated | admin.session_resent
    before_status = Column(String(20), nullable=True)
    after_status  = Column(String(20), nullable=True)
    detail        = Column(JSON, nullable=True)


class ProcessedStripeEvent(Base):
    """Event-level dedup so Stripe retries / overlapping deliveries
    can't both pass the per-row idempotency check and double-credit
    Surgery.amount_paid. The webhook receiver inserts here in the
    same transaction as the row mutation; the PK collision rolls
    everything back. (Fable billing audit H2.)
    """
    __tablename__ = "processed_stripe_events"
    event_id  = Column(String(80), primary_key=True)
    received_at = Column(DateTime, default=now_utc_naive, nullable=False)
    event_type = Column(String(60), nullable=True)
