"""Authenticated end-to-end walk-through of the pellet SUBSCRIPTION flow,
driving the real endpoints + webhook handlers (Stripe mocked):
subscribe -> monthly invoice.paid accrues credit -> credit unlocks scheduling
-> patient books -> staff completes (draws down accrued credit) -> cancel.

Run: pytest tests/test_pellet_subscription_walkthrough.py -s
"""
from datetime import date, time, timedelta
from decimal import Decimal
import pytest

from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
from app.models.pellet_payment import PelletSubscription
from app.models.pellet_config import PelletConfig
from app.models.pellet_schedule import PelletSlot
from app.services.pellet import portal_auth
from app.services.pellet import payments as pay
from app.utils.dt import now_utc_naive


class _FakeSub:
    id = "sub_wt"
    status = "active"


def test_subscription_walkthrough(client, db, capsys, monkeypatch):
    log = []
    # Config: $100/mo subscription, $400 insertion (default).
    db.add(PelletConfig(key="subscription_monthly_amount", value=100.0))
    db.commit()
    # A scheduling-ready patient: mammo + labs verified, valid consent.
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      patient_email="j@x.com", mammo_verified=True, labs_verified=True)
    db.add(p); db.flush()
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="e1", status="signed",
                         signed_at=now_utc_naive(), expires_at=now_utc_naive() + timedelta(days=300)))
    db.commit(); db.refresh(p)
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}

    # 1. Patient subscribes (Stripe mocked).
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_get_or_create_pellet_customer", lambda db, p: "cus_wt")
    monkeypatch.setattr(pay, "_create_stripe_subscription", lambda **kw: (_FakeSub(), "price_1"))
    r = client.post("/api/pellet-portal/payment/subscribe", headers=h)
    assert r.status_code == 200, r.text
    sub = db.query(PelletSubscription).filter_by(pellet_patient_id=p.id).first()
    assert sub.status == "active" and float(sub.accrued_credit) == 0.0
    log.append("1. patient subscribed ($100/mo) -> PelletSubscription active, accrued $0")

    # 2. Four monthly invoice.paid webhooks accrue credit to $400.
    for i in range(1, 5):
        pay.handle_pellet_invoice_paid(db, {"id": f"in_{i}", "subscription": "sub_wt",
                                            "amount_paid": 10000}); db.commit()
    db.refresh(sub)
    assert sub.accrued_credit == Decimal("400.00")
    log.append("2. 4x monthly invoice.paid webhooks -> accrued credit $400.00")

    # 3. Status now shows 1 available insertion (floor(400/400)).
    status = client.get("/api/pellet-portal/payment/status", headers=h).json()
    assert status["available_insertions"] == 1
    assert status["subscription"]["accrued_credit"] == 400.0
    log.append(f"3. payment status: available_insertions={status['available_insertions']} "
               f"(subscription accrued ${status['subscription']['accrued_credit']:.2f})")

    # 4. Credit unlocks scheduling — patient books an open slot.
    db.add(PelletSlot(location="white_plains", slot_date=date(2099, 7, 1),
                      start_time=time(9), end_time=time(10), status="open"))
    db.commit()
    sid = client.get("/api/pellet-portal/schedule/slots?location=white_plains",
                     headers=h).json()["items"][0]["id"]
    r = client.post(f"/api/pellet-portal/schedule/slots/{sid}/book", headers=h)
    assert r.status_code == 200, r.text
    log.append("4. credit unlocked scheduling -> patient booked an insertion slot")

    # 5. Staff completes the visit -> draws down from subscription accrued credit.
    r = client.post(f"/api/pellets/slots/{sid}/complete")
    assert r.status_code == 200, r.text
    db.refresh(sub)
    assert sub.accrued_credit == Decimal("0.00")          # 400 - 400 insertion price
    assert pay.available_insertions(db, p) == 0
    log.append("5. staff completed insertion -> drew down $400 from subscription (accrued back to $0)")

    # 6. Subscription cancellation webhook -> status canceled, accrued credit retained.
    sub.accrued_credit = Decimal("150.00"); db.commit()    # say some credit had re-accrued
    pay.handle_pellet_subscription_event(db, "customer.subscription.deleted",
                                         {"id": "sub_wt", "status": "canceled"}); db.commit()
    db.refresh(sub)
    assert sub.status == "canceled" and sub.accrued_credit == Decimal("150.00")
    log.append("6. customer.subscription.deleted -> status canceled, accrued credit kept ($150)")

    with capsys.disabled():
        print("\n  -- Pellet subscription walk-through (authenticated) --")
        for line in log:
            print("   " + line)
