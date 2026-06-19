"""Stripe payment endpoints (H3)."""
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.stripe_payment import SurgeryPayment, SurgeryPaymentHistory
from app.models.surgery import Surgery


def _make_surgery(db, **kw):
    defaults = dict(
        patient_responsibility=Decimal("750.00"),
        amount_paid=Decimal("0"),
    )
    defaults.update(kw)
    s = Surgery(
        chart_number="A001",
        patient_name="Jane Doe",
        email="jane@example.com",
        eligible_facilities=["medstar"],
        selected_facility="medstar",
        status="confirmed",
        **defaults,
    )
    db.add(s); db.commit(); db.refresh(s)
    return s


# ─── /request-payment ──────────────────────────────────────────────

def test_request_payment_requires_stripe_configured(client, db, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    s = _make_surgery(db)
    resp = client.post(f"/api/surgery/{s.id}/request-payment", json={})
    assert resp.status_code == 503


def test_request_payment_creates_session(client, db, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    s = _make_surgery(db)
    mock_pay = MagicMock(
        id="payment_uuid", status="requested",
        amount_requested=Decimal("750.00"), amount_paid=Decimal("0"),
        amount_refunded=Decimal("0"), currency="usd",
        description="Pre-op balance",
        checkout_url="https://checkout.stripe.com/cs_test_99",
        requested_by="tester@waldorfwomenscare.com",
    )
    # Make .id render as a string
    mock_pay.id = "payment_uuid"
    mock_pay.requested_at = None; mock_pay.paid_at = None
    mock_pay.refunded_at = None;  mock_pay.failed_at = None
    mock_pay.failure_reason = None
    with patch("app.routers.stripe_payments.svc.create_checkout_session",
                return_value=mock_pay):
        resp = client.post(f"/api/surgery/{s.id}/request-payment",
                            json={"description": "Pre-op balance"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["checkout_url"].startswith("https://checkout.stripe.com/")
    assert body["status"] == "requested"


def test_request_payment_rejects_when_no_balance(client, db, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    s = _make_surgery(db, patient_responsibility=Decimal("0"))
    resp = client.post(f"/api/surgery/{s.id}/request-payment", json={})
    assert resp.status_code == 422


# ─── /payments listing ─────────────────────────────────────────────

def test_list_payments_returns_balance_and_history(client, db):
    s = _make_surgery(db)
    p = SurgeryPayment(
        surgery_id=s.id, amount_requested=Decimal("750.00"),
        requested_by="tester@x.com", status="requested",
    )
    db.add(p); db.commit()
    resp = client.get(f"/api/surgery/{s.id}/payments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["outstanding_balance"] == "750.00"
    assert body["patient_responsibility"] == "750.00"
    assert len(body["payments"]) == 1


# ─── /webhook ──────────────────────────────────────────────────────

def test_webhook_rejects_bad_signature(client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_xxx")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    with patch("app.routers.stripe_payments.svc.parse_webhook_event",
                side_effect=ValueError("bad sig")):
        resp = client.post("/api/stripe/webhook",
                            content=b'{"x":1}',
                            headers={"stripe-signature": "bogus"})
    assert resp.status_code == 400


def test_webhook_session_completed_marks_paid(client, db, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_xxx")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    s = _make_surgery(db)
    p = SurgeryPayment(
        surgery_id=s.id,
        stripe_checkout_session_id="cs_test_99",
        amount_requested=Decimal("750.00"),
        requested_by="tester@x.com",
        status="requested",
    )
    db.add(p); db.commit()

    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_99",
            "amount_total": 75000,
            "payment_intent": "pi_test_99",
            "payment_status": "paid",
        }},
    }
    with patch("app.routers.stripe_payments.svc.parse_webhook_event",
                return_value=event):
        resp = client.post("/api/stripe/webhook",
                            content=b'{}',
                            headers={"stripe-signature": "abc"})
    assert resp.status_code == 200
    db.refresh(p); db.refresh(s)
    assert p.status == "paid"
    assert p.amount_paid == Decimal("750.00")
    assert p.stripe_payment_intent_id == "pi_test_99"
    assert s.amount_paid == Decimal("750.00")
    # History row written
    h = (db.query(SurgeryPaymentHistory)
           .filter(SurgeryPaymentHistory.payment_id == p.id).first())
    assert h.event_type == "checkout.session.completed"


def test_webhook_refund_decrements_amount_paid(client, db, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_xxx")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    s = _make_surgery(db, amount_paid=Decimal("750.00"))
    p = SurgeryPayment(
        surgery_id=s.id,
        stripe_checkout_session_id="cs_a",
        stripe_payment_intent_id="pi_a",
        amount_requested=Decimal("750.00"),
        amount_paid=Decimal("750.00"),
        status="paid",
        requested_by="tester@x.com",
    )
    db.add(p); db.commit()

    event = {
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": "pi_a", "amount_refunded": 75000}},
    }
    with patch("app.routers.stripe_payments.svc.parse_webhook_event",
                return_value=event):
        client.post("/api/stripe/webhook", content=b'{}',
                     headers={"stripe-signature": "abc"})
    db.refresh(p); db.refresh(s)
    assert p.status == "refunded"
    assert p.amount_refunded == Decimal("750.00")
    assert s.amount_paid == Decimal("0")


# ─── /refund ───────────────────────────────────────────────────────

def test_refund_rejects_non_paid_payment(client, db):
    s = _make_surgery(db)
    p = SurgeryPayment(surgery_id=s.id, amount_requested=Decimal("100"),
                        requested_by="t@x.com", status="requested")
    db.add(p); db.commit()
    resp = client.post(f"/api/surgery/payments/{p.id}/refund", json={})
    assert resp.status_code == 409


def test_request_payment_sends_link_email(client, db, monkeypatch):
    from unittest.mock import MagicMock, patch
    from decimal import Decimal

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")

    # Seed both an active template + a surgery
    from app.models.patient_email import EmailTemplate, PatientEmail
    db.add(EmailTemplate(
        kind="stripe_payment_link", label="x",
        subject="Pay {{amount}}", html_body="<p>Link: {{checkout_url}}</p>",
    ))
    s = _make_surgery(db)
    db.commit()

    mock_pay = MagicMock(
        id="pay_uuid", status="requested",
        amount_requested=Decimal("750.00"), amount_paid=Decimal("0"),
        amount_refunded=Decimal("0"), currency="usd",
        description="Pre-op balance",
        checkout_url="https://checkout.stripe.com/cs_test_99",
        requested_by="tester@waldorfwomenscare.com",
    )
    for attr in ("requested_at", "paid_at", "refunded_at", "failed_at",
                  "failure_reason"):
        setattr(mock_pay, attr, None)

    with patch("app.routers.stripe_payments.svc.create_checkout_session",
                return_value=mock_pay), \
         patch("app.services.patient_email.send_email", return_value=True):
        resp = client.post(f"/api/surgery/{s.id}/request-payment", json={})

    assert resp.status_code == 200
    emails = (db.query(PatientEmail)
                .filter(PatientEmail.template_kind == "stripe_payment_link")
                .all())
    assert len(emails) == 1
    assert emails[0].to_email == s.email
    assert "cs_test_99" in emails[0].rendered_html
    assert emails[0].status == "sent"


def test_webhook_session_completed_sends_receipt(client, db, monkeypatch):
    from unittest.mock import patch
    from decimal import Decimal

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_xxx")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")

    from app.models.patient_email import EmailTemplate, PatientEmail
    from app.models.stripe_payment import SurgeryPayment
    db.add(EmailTemplate(
        kind="stripe_payment_receipt", label="x",
        subject="Thanks {{amount}}",
        html_body="<p>Received {{amount}} for {{surgery_date}}</p>",
    ))
    s = _make_surgery(db)
    p = SurgeryPayment(
        surgery_id=s.id, stripe_checkout_session_id="cs_test_recpt",
        amount_requested=Decimal("750.00"), requested_by="tester@x.com",
        status="requested",
    )
    db.add(p); db.commit()

    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_recpt",
            "amount_total": 75000,
            "payment_intent": "pi_recpt",
            "payment_status": "paid",
        }},
    }
    with patch("app.routers.stripe_payments.svc.parse_webhook_event",
                return_value=event), \
         patch("app.services.patient_email.send_email", return_value=True):
        resp = client.post("/api/stripe/webhook", content=b'{}',
                            headers={"stripe-signature": "abc"})
    assert resp.status_code == 200

    emails = (db.query(PatientEmail)
                .filter(PatientEmail.template_kind == "stripe_payment_receipt")
                .all())
    assert len(emails) == 1
    assert emails[0].to_email == s.email
    assert "750.00" in emails[0].rendered_html


def test_webhook_fmla_fee_sets_fmla_fee_paid_no_balance_bump(client, db):
    """A paid SurgeryPayment with kind='fmla_fee' must:
      - set Surgery.fmla_fee_paid = True + fmla_fee_paid_at + fmla_fee_stripe_session_id
      - NOT bump Surgery.amount_paid
      - auto-flip fmla_status to 'submitted' if blank upload exists
      - NOT send the patient_balance receipt email
    """
    from decimal import Decimal
    from unittest.mock import patch
    from app.models.surgery import Surgery, SurgeryDocument
    from app.models.stripe_payment import SurgeryPayment
    from app.routers.stripe_payments import _handle_session_completed

    s = Surgery(chart_number="FMLA-1", patient_name="Pat", status="new",
                  patient_responsibility=Decimal("500.00"),
                  amount_paid=Decimal("100.00"),
                  email="p@example.com")
    db.add(s); db.commit(); db.refresh(s)
    db.add(SurgeryDocument(
        surgery_id=s.id, kind="fmla_blank",
        filename="my_fmla.pdf",
        gcs_path=f"surgery-uploads/{s.id}/fmla_blank/x.pdf",
        uploaded_by="patient:portal",
    ))
    pay = SurgeryPayment(
        surgery_id=s.id, status="requested", kind="fmla_fee",
        amount_requested=Decimal("25.00"),
        amount_paid=Decimal("0.00"), amount_refunded=Decimal("0.00"),
        currency="usd", requested_by="patient:portal:fmla",
        stripe_checkout_session_id="cs_test_fmla_webhook",
    )
    db.add(pay); db.commit(); db.refresh(pay)

    with patch("app.routers.stripe_payments.send_patient_email") as mock_email:
        _handle_session_completed(db, "checkout.session.completed", {
            "id": "cs_test_fmla_webhook",
            "amount_total": 2500,
            "payment_intent": "pi_test_fmla",
            "payment_status": "paid",
        })

    db.refresh(s); db.refresh(pay)
    assert pay.status == "paid"
    assert pay.amount_paid == Decimal("25.00")
    assert s.fmla_fee_paid is True
    assert s.fmla_fee_paid_at is not None
    assert s.fmla_fee_stripe_session_id == "cs_test_fmla_webhook"
    assert s.fmla_status == "submitted"
    # amount_paid unchanged — FMLA fee doesn't touch it
    assert s.amount_paid == Decimal("100.00")
    # No surgery-balance receipt email for FMLA fees
    mock_email.assert_not_called()


def test_webhook_patient_balance_unchanged_behavior(client, db):
    """Existing patient_balance path must still bump Surgery.amount_paid
    and send the receipt email — T2 must not regress this."""
    from decimal import Decimal
    from unittest.mock import patch
    from app.models.surgery import Surgery
    from app.models.stripe_payment import SurgeryPayment
    from app.routers.stripe_payments import _handle_session_completed

    s = Surgery(chart_number="BAL-1", patient_name="Pat", status="new",
                  patient_responsibility=Decimal("500.00"),
                  amount_paid=Decimal("100.00"),
                  email="p@example.com")
    db.add(s); db.commit(); db.refresh(s)
    pay = SurgeryPayment(
        surgery_id=s.id, status="requested",  # kind defaults to patient_balance
        amount_requested=Decimal("200.00"),
        amount_paid=Decimal("0.00"), amount_refunded=Decimal("0.00"),
        currency="usd", requested_by="patient:portal",
        stripe_checkout_session_id="cs_test_balance",
    )
    db.add(pay); db.commit(); db.refresh(pay)

    with patch("app.routers.stripe_payments.send_patient_email"):
        _handle_session_completed(db, "checkout.session.completed", {
            "id": "cs_test_balance",
            "amount_total": 20000,
            "payment_intent": "pi_test_balance",
            "payment_status": "paid",
        })

    db.refresh(s); db.refresh(pay)
    assert pay.kind == "patient_balance"
    assert s.amount_paid == Decimal("300.00")  # 100 + 200
    # FMLA flags untouched
    assert s.fmla_fee_paid is False
