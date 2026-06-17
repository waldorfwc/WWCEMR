"""Authenticated walk-through of the pellet portal Receipts flow: a patient
lists their paid receipts and resolves the Stripe-hosted receipt for each
(charge receipt for single/package, hosted invoice for subscription); a
receipt belonging to another patient is not resolvable.
"""
from datetime import date
from decimal import Decimal
import pytest

from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletPayment
from app.services.pellet import portal_auth
from app.services.pellet import payments as pay
from app.utils.dt import now_utc_naive


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


class _Charge:
    receipt_url = "https://stripe.test/receipt/charge_1"


class _PI:
    latest_charge = _Charge()


class _Invoice:
    hosted_invoice_url = "https://stripe.test/invoice/inv_1"


class _FakeStripe:
    class PaymentIntent:
        @staticmethod
        def retrieve(pid, **kw):
            return _PI()

    class Invoice:
        @staticmethod
        def retrieve(iid, **kw):
            return _Invoice()


def test_receipts_walkthrough(client, db, auth, capsys, monkeypatch):
    log = []
    p, h = auth
    db.add(PelletPayment(pellet_patient_id=p.id, kind="single", amount=Decimal("400.00"),
                         status="paid", requested_by="patient",
                         stripe_payment_intent_id="pi_1", paid_at=now_utc_naive()))
    db.add(PelletPayment(pellet_patient_id=p.id, kind="subscription_invoice",
                         amount=Decimal("100.00"), status="paid", requested_by="stripe",
                         stripe_invoice_id="in_1", paid_at=now_utc_naive()))
    db.commit()

    # 1. List shows both paid receipts with a resolvable-receipt flag.
    items = client.get("/api/pellet-portal/receipts", headers=h).json()["items"]
    assert len(items) == 2 and all(it["has_receipt"] for it in items)
    by_kind = {it["kind"]: it for it in items}
    log.append(f"1. /receipts → 2 paid: Single Insertion ${by_kind['single']['amount']:.2f}, "
               f"Subscription ${by_kind['subscription_invoice']['amount']:.2f}")

    # 2. Resolve the Stripe-hosted receipt for each (Stripe mocked).
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_client", lambda: _FakeStripe)
    single_url = client.get(f"/api/pellet-portal/receipts/{by_kind['single']['id']}/receipt-url",
                            headers=h).json()["url"]
    sub_url = client.get(f"/api/pellet-portal/receipts/{by_kind['subscription_invoice']['id']}/receipt-url",
                         headers=h).json()["url"]
    assert single_url == "https://stripe.test/receipt/charge_1"     # PaymentIntent → charge receipt
    assert sub_url == "https://stripe.test/invoice/inv_1"           # invoice → hosted invoice
    log.append("2. View Receipt → single resolves the charge receipt; subscription resolves the hosted invoice")

    # 3. A receipt belonging to another patient is not resolvable for this patient.
    other = PelletPatient(patient_name="Other, Pat", chart_number="MRN9",
                          patient_dob=date(1980, 1, 1), patient_phone="3015550000")
    db.add(other); db.flush()
    orow = PelletPayment(pellet_patient_id=other.id, kind="single", amount=Decimal("400"),
                         status="paid", requested_by="patient", stripe_payment_intent_id="pi_o")
    db.add(orow); db.commit()
    assert client.get(f"/api/pellet-portal/receipts/{orow.id}/receipt-url", headers=h).status_code == 404
    log.append("3. another patient's receipt → 404 (ownership enforced)")

    with capsys.disabled():
        print("\n  -- Pellet portal receipts walk-through (authenticated) --")
        for line in log:
            print("   " + line)
