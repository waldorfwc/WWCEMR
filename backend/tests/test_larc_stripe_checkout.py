"""LARC Stripe checkout — patient-responsibility charge. No real API calls."""
from decimal import Decimal
from unittest.mock import patch, MagicMock

from app.models.larc import LarcAssignment
from app.models.larc_payment import LarcPayment
from app.services.stripe_payments import create_larc_checkout


def _make_assignment(db):
    a = LarcAssignment(
        chart_number="L001",
        patient_name="Jane Doe",
        patient_email="jane@example.com",
        source_flow="in_stock",
        status="new",
        patient_responsibility=Decimal("250.00"),
    )
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_create_larc_checkout_persists_payment(db, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    a = _make_assignment(db)

    fake_session = MagicMock(id="cs_test_larc", url="https://stripe.test/larc")
    with patch("app.services.stripe_payments._client") as mock_cli:
        mock_cli.return_value.checkout.Session.create.return_value = fake_session
        out = create_larc_checkout(db, a, amount=Decimal("250.00"))

    assert out["checkout_url"] == "https://stripe.test/larc"

    row = (db.query(LarcPayment)
             .filter(LarcPayment.assignment_id == str(a.id))
             .first())
    assert row is not None
    assert row.status == "requested"
    assert row.stripe_checkout_session_id == "cs_test_larc"
    assert row.checkout_url == "https://stripe.test/larc"
    assert row.kind == "larc_patient_responsibility"
