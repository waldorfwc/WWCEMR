"""Stripe service contract — no real API calls (everything mocked)."""
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest

from app.models.stripe_payment import StripeCustomer, SurgeryPayment
from app.models.surgery import Surgery
from app.services.stripe_payments import (
    create_checkout_session, get_or_create_customer, parse_webhook_event,
    is_configured,
)


def _make_surgery(db):
    s = Surgery(
        chart_number="A001",
        patient_name="Jane Doe",
        email="jane@example.com",
        eligible_facilities=["medstar"],
        selected_facility="medstar",
        status="confirmed",
        patient_responsibility=Decimal("750.00"),
    )
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_is_configured_reflects_env(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert is_configured() is False
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    assert is_configured() is True


def test_get_or_create_customer_creates_then_reuses(db, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    s = _make_surgery(db)

    mock_customer = MagicMock(id="cus_test_1")
    with patch("app.services.stripe_payments._client") as mock_cli:
        mock_cli.return_value.Customer.create.return_value = mock_customer
        cid1 = get_or_create_customer(db, s)
    assert cid1 == "cus_test_1"
    # Row persisted
    row = db.query(StripeCustomer).filter_by(chart_number="A001").first()
    assert row.stripe_customer_id == "cus_test_1"

    # Second call returns existing without hitting Stripe again
    with patch("app.services.stripe_payments._client") as mock_cli:
        cid2 = get_or_create_customer(db, s)
        assert mock_cli.return_value.Customer.create.call_count == 0
    assert cid2 == cid1


def test_create_checkout_session_persists_row(db, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    s = _make_surgery(db)

    mock_session = MagicMock(id="cs_test_99", url="https://checkout.stripe.com/cs_test_99")
    with patch("app.services.stripe_payments._client") as mock_cli:
        mock_cli.return_value.Customer.create.return_value = MagicMock(id="cus_test_2")
        mock_cli.return_value.checkout.Session.create.return_value = mock_session
        pay = create_checkout_session(
            db, s,
            amount=Decimal("750.00"),
            description="Pre-op balance",
            actor="ocooke@example.com",
        )
    assert pay.stripe_checkout_session_id == "cs_test_99"
    assert pay.checkout_url == "https://checkout.stripe.com/cs_test_99"
    assert pay.amount_requested == Decimal("750.00")
    assert pay.status == "requested"
    assert pay.requested_by == "ocooke@example.com"

    # Stripe call sent amount in cents
    args = mock_cli.return_value.checkout.Session.create.call_args
    assert args.kwargs["line_items"][0]["price_data"]["unit_amount"] == 75000


def test_create_checkout_session_rejects_zero(db, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    s = _make_surgery(db)
    with pytest.raises(ValueError):
        create_checkout_session(db, s, amount=Decimal("0"),
                                  description="bad", actor="x@y.com")


def test_parse_webhook_requires_secret(monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    with pytest.raises(ValueError):
        parse_webhook_event(b"{}", "sig")


def test_parse_webhook_verifies_signature(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_xxx")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    mock_event = {"id": "evt_1", "type": "checkout.session.completed",
                   "data": {"object": {"id": "cs_test_99"}}}
    with patch("app.services.stripe_payments._client") as mock_cli:
        mock_cli.return_value.Webhook.construct_event.return_value = mock_event
        out = parse_webhook_event(b'{"x":1}', "t=123,v1=abc")
    assert out["type"] == "checkout.session.completed"
