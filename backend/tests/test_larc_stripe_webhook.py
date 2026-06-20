"""LARC Stripe webhook branch — completed checkout marks paid, stamps the
assignment, fires the receipt notification, and auto-allocates. No real API."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.models.larc import LarcAssignment, LarcDevice, LarcDeviceType
from app.models.larc_payment import LarcPayment
from app.models.patient_email import PatientEmail
from app.utils.dt import now_utc_naive


def test_webhook_larc_session_completed_pays_and_allocates(client, db, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_xxx")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")

    dt = LarcDeviceType(name="Liletta", category="larc",
                        default_flow="in_stock", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    # Unassigned device of the same type so auto-allocate can succeed.
    db.add(LarcDevice(our_id="W-1", device_type_id=dt.id,
                      status="unassigned", ownership="wwc_owned"))

    a = LarcAssignment(
        chart_number="L100",
        patient_name="Doe, Jane",
        patient_email="jane@example.com",
        device_type_id=dt.id,
        source_flow="in_stock",
        status="in_progress",
        benefits_verified_at=date.today(),
    )
    db.add(a); db.commit(); db.refresh(a)

    pay = LarcPayment(
        assignment_id=str(a.id),
        status="requested",
        stripe_checkout_session_id="cs_test_larc",
        amount_requested=Decimal("120.00"),
    )
    db.add(pay); db.commit(); db.refresh(pay)

    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_larc",
            "amount_total": 12000,
            "payment_intent": "pi_test",
            "payment_status": "paid",
            "metadata": {"larc_assignment_id": str(a.id)},
        }},
    }
    with patch("app.routers.stripe_payments.svc.parse_webhook_event",
               return_value=event):
        resp = client.post("/api/stripe/webhook", content=b'{}',
                           headers={"stripe-signature": "abc"})
    assert resp.status_code == 200

    db.refresh(pay); db.refresh(a)
    assert pay.status == "paid"
    assert pay.amount_paid == Decimal("120.00")
    assert pay.stripe_payment_intent_id == "pi_test"
    assert a.patient_paid_at is not None
    assert a.device_id is not None  # auto-allocated

    receipt = (db.query(PatientEmail)
                 .filter(PatientEmail.template_kind == "larc_payment_receipt",
                         PatientEmail.larc_assignment_id == a.id)
                 .first())
    assert receipt is not None
