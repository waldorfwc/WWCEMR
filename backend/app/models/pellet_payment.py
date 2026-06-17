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
    kind = Column(String(30), nullable=False)   # single|package|subscription_invoice|manual
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
    source = Column(String(30), nullable=False)   # single|package|subscription|consume|adjustment
    reason = Column(Text, nullable=True)
    payment_id = Column(GUID(), nullable=True)
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
    accrued_credit = Column(Numeric(10, 2), default=0, nullable=False)
    status = Column(String(20), default="active", nullable=False)  # active|canceled|past_due
    started_at = Column(DateTime, default=now_utc_naive, nullable=False)
    canceled_at = Column(DateTime, nullable=True)
