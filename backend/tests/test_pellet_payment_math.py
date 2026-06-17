from datetime import date
from decimal import Decimal
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletInsertionCredit, PelletSubscription
from app.services.pellet import payments as pay


def _patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_package_price_applies_tier_discount(db):
    assert pay.package_price(db, 2) == Decimal("760.00")
    assert pay.package_price(db, 3) == Decimal("1080.00")
    assert pay.package_price(db, 4) == Decimal("1360.00")
    assert pay.package_price(db, 1) == Decimal("400.00")


def test_credit_balance_and_available(db):
    p = _patient(db)
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=2, source="package"))
    db.commit()
    assert pay.credit_balance(db, p) == 2
    db.add(PelletSubscription(pellet_patient_id=p.id, monthly_amount=Decimal("100"),
                              accrued_credit=Decimal("850"), status="active"))
    db.commit()
    assert pay.available_insertions(db, p) == 4


def test_consume_prefers_credit_then_subscription(db):
    p = _patient(db)
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    db.add(PelletSubscription(pellet_patient_id=p.id, monthly_amount=Decimal("100"),
                              accrued_credit=Decimal("400"), status="active"))
    db.commit()
    pay.consume_insertion(db, p, by="staff@x"); db.commit()
    assert pay.credit_balance(db, p) == 0
    sub = db.query(PelletSubscription).filter(PelletSubscription.pellet_patient_id == p.id).first()
    assert sub.accrued_credit == Decimal("400")
    pay.consume_insertion(db, p, by="staff@x"); db.commit()
    db.refresh(sub)
    assert sub.accrued_credit == Decimal("0")


def test_consume_raises_when_no_credit(db):
    p = _patient(db)
    with pytest.raises(pay.InsufficientCredit):
        pay.consume_insertion(db, p, by="staff@x")
