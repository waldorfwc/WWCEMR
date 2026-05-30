"""Smoke tests for the Stripe payment data model (H1)."""
from datetime import datetime
from decimal import Decimal

from app.models.stripe_payment import (
    StripeCustomer, SurgeryPayment, SurgeryPaymentHistory,
    SURGERY_PAYMENT_STATUSES,
)
from app.models.surgery import Surgery


def _make_surgery(db, **kw):
    s = Surgery(
        chart_number="1234",
        patient_name="Jane Doe",
        eligible_facilities=["medstar"],
        selected_facility="medstar",
        status="confirmed",
        patient_responsibility=Decimal("750.00"),
        **kw,
    )
    db.add(s); db.flush()
    return s


def test_stripe_customer_round_trip(db):
    db.add(StripeCustomer(
        chart_number="1234",
        stripe_customer_id="cus_test_1",
        email="jane@example.com",
        name="Jane Doe",
    ))
    db.commit()

    row = db.query(StripeCustomer).filter_by(chart_number="1234").first()
    assert row.stripe_customer_id == "cus_test_1"


def test_surgery_payment_create_and_relate(db):
    s = _make_surgery(db)
    p = SurgeryPayment(
        surgery_id=s.id,
        stripe_checkout_session_id="cs_test_1",
        amount_requested=Decimal("750.00"),
        requested_by="ocooke@example.com",
        description="Pre-op balance",
    )
    db.add(p); db.commit()

    db.refresh(s)
    assert len(s.payments) == 1
    assert s.payments[0].amount_requested == Decimal("750.00")
    assert s.payments[0].status == "requested"


def test_payment_history_audit(db):
    s = _make_surgery(db)
    p = SurgeryPayment(
        surgery_id=s.id, amount_requested=Decimal("100"),
        requested_by="x@y.com",
    )
    db.add(p); db.flush()
    db.add(SurgeryPaymentHistory(
        payment_id=p.id, actor="stripe:webhook",
        event_type="checkout.session.completed",
        before_status="requested", after_status="paid",
    ))
    db.commit()

    h = db.query(SurgeryPaymentHistory).filter_by(payment_id=p.id).first()
    assert h.event_type == "checkout.session.completed"
    assert h.after_status == "paid"


def test_payment_statuses_constant():
    assert "paid"      in SURGERY_PAYMENT_STATUSES
    assert "refunded"  in SURGERY_PAYMENT_STATUSES
    assert "failed"    in SURGERY_PAYMENT_STATUSES
    assert "expired"   in SURGERY_PAYMENT_STATUSES
    assert "requested" in SURGERY_PAYMENT_STATUSES
