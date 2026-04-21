"""Tests for the one-time orphan Payment cleanup."""
from datetime import date
from decimal import Decimal
import pytest
from app.models.claim import Claim, ClaimStatus
from app.models.patient import Patient
from app.models.payment import Payment, PaymentType


def _seed(db):
    p = Patient(patient_id="P1", first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    c = Claim(claim_number="C1", patient_id=p.id, status=ClaimStatus.PENDING,
              billed_amount=Decimal("100"), balance=Decimal("0"))
    db.add(c); db.commit(); db.refresh(c)
    # Valid payment tied to existing claim
    db.add(Payment(claim_id=c.id, payment_type=PaymentType.INSURANCE_PAYMENT,
                   amount=Decimal("50"), payment_date=date.today()))
    # Orphan payment with fabricated claim_id
    db.add(Payment(claim_id="00000000-0000-0000-0000-000000000099",
                   payment_type=PaymentType.INSURANCE_PAYMENT,
                   amount=Decimal("25"), payment_date=date.today()))
    db.commit()
    return c


def test_prune_deletes_only_orphans(db):
    from app.scripts.prune_orphan_payments import run
    c = _seed(db)
    assert db.query(Payment).count() == 2
    deleted = run(confirm=True, session=db)
    assert deleted == 1
    remaining = db.query(Payment).all()
    assert len(remaining) == 1
    assert remaining[0].claim_id == c.id


def test_prune_refuses_without_confirm_flag(db):
    from app.scripts.prune_orphan_payments import run
    _seed(db)
    with pytest.raises(SystemExit):
        run(confirm=False, session=db)
    assert db.query(Payment).count() == 2
