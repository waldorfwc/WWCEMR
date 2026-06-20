"""Stripe payment rows for a LARC patient-responsibility charge. Mirrors PelletPayment."""
from __future__ import annotations

from app.utils.dt import now_utc_naive
from sqlalchemy import Column, DateTime, JSON, Numeric, String

from app.database import Base
from app.models.guid import GUID, new_uuid


class LarcPayment(Base):
    __tablename__ = "larc_payments"

    id            = Column(GUID(), primary_key=True, default=new_uuid)
    assignment_id = Column(String(36), index=True, nullable=False)
    kind          = Column(String(40), default="larc_patient_responsibility", nullable=False)
    status        = Column(String(20), default="requested", nullable=False)  # requested|paid|failed|expired|refunded
    amount_requested = Column(Numeric(10, 2), nullable=True)
    amount_paid      = Column(Numeric(10, 2), nullable=True)
    stripe_checkout_session_id = Column(String(255), index=True, nullable=True)
    stripe_payment_intent_id   = Column(String(255), index=True, nullable=True)
    checkout_url     = Column(String(600), nullable=True)
    last_event_payload = Column(JSON, nullable=True)
    requested_at = Column(DateTime, default=now_utc_naive, nullable=False)
    paid_at      = Column(DateTime, nullable=True)
