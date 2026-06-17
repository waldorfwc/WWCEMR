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
