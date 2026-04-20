"""Unit tests for the balance recompute utility."""
from decimal import Decimal
from app.models.claim import Claim
from app.services.claim_math import recompute_balance


def _make_claim(**kw) -> Claim:
    defaults = dict(
        billed_amount=Decimal("0"),
        allowed_amount=Decimal("0"),
        paid_amount=Decimal("0"),
        patient_responsibility=Decimal("0"),
        contractual_adjustment=Decimal("0"),
        other_adjustment=Decimal("0"),
        balance=Decimal("0"),
    )
    defaults.update(kw)
    return Claim(**defaults)


def test_recompute_balance_basic():
    c = _make_claim(
        billed_amount=Decimal("100"),
        contractual_adjustment=Decimal("10"),
        paid_amount=Decimal("80"),
        patient_responsibility=Decimal("5"),
    )
    recompute_balance(c)
    assert float(c.balance) == 5.0


def test_recompute_balance_zeros():
    c = _make_claim()
    recompute_balance(c)
    assert float(c.balance) == 0.0


def test_recompute_balance_negative_adjustment_increases_balance():
    c = _make_claim(
        billed_amount=Decimal("100"),
        contractual_adjustment=Decimal("-20"),
    )
    recompute_balance(c)
    assert float(c.balance) == 120.0


def test_recompute_balance_handles_none_fields():
    c = Claim(billed_amount=Decimal("50"))  # other money fields left None
    recompute_balance(c)
    assert float(c.balance) == 50.0


def test_recompute_balance_is_idempotent():
    c = _make_claim(billed_amount=Decimal("100"), paid_amount=Decimal("25"))
    recompute_balance(c)
    first = float(c.balance)
    recompute_balance(c)
    assert float(c.balance) == first == 75.0


def test_recompute_balance_does_not_touch_other_fields():
    c = _make_claim(billed_amount=Decimal("100"), notes="keep me")
    c.status = "pending"
    recompute_balance(c)
    assert c.notes == "keep me"
    assert c.status == "pending"
