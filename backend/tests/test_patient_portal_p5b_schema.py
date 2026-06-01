"""Patient portal P5b schema — FMLA fee tracking."""
from decimal import Decimal

from app.models.surgery import Surgery
from app.models.stripe_payment import SurgeryPayment


def test_surgery_has_fmla_fee_columns(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    assert s.fmla_fee_paid is False
    assert s.fmla_fee_paid_at is None
    assert s.fmla_fee_stripe_session is None


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
