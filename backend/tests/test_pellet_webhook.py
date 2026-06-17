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
    assert pay.credit_balance(db, p) == 1


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


def test_invoice_paid_idempotent(db):
    p = _patient(db)
    db.add(PelletSubscription(pellet_patient_id=p.id, stripe_subscription_id="sub_2",
                              monthly_amount=Decimal("100"), accrued_credit=Decimal("0"),
                              status="active"))
    db.commit()
    obj = {"id": "in_2", "subscription": "sub_2", "amount_paid": 10000}
    pay.handle_pellet_invoice_paid(db, obj); db.commit()
    pay.handle_pellet_invoice_paid(db, obj); db.commit()   # replay
    sub = db.query(PelletSubscription).filter_by(stripe_subscription_id="sub_2").first()
    assert sub.accrued_credit == Decimal("100.00")   # not 200


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
    assert sub.accrued_credit == Decimal("250")
