"""Authenticated Phase-2 walk-through: patient buys a 3-package (mocked
Stripe), the webhook grants 3 credits, status shows them, staff draws one
down on completion."""
from datetime import date
from decimal import Decimal
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletPayment
from app.services.pellet import portal_auth
from app.services.pellet import payments as pay


class _FakeSession:
    id = "cs_wt"; url = "https://stripe.test/cs_wt"


def test_phase2_walkthrough(client, db, capsys, monkeypatch):
    log = []
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_get_or_create_pellet_customer", lambda db, p: "cus_wt")
    monkeypatch.setattr(pay, "_create_checkout_session_obj", lambda **kw: _FakeSession())

    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      patient_email="j@x.com")
    db.add(p); db.commit(); db.refresh(p)
    h = {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}

    r = client.post("/api/pellet-portal/payment/package", json={"count": 3}, headers=h)
    assert r.status_code == 200, r.text
    row = db.query(PelletPayment).filter_by(pellet_patient_id=p.id, kind="package").first()
    assert float(row.amount) == 1080.0
    log.append(f"1. bought 3-package -> ${float(row.amount):.2f} (10% off), checkout requested")

    pay.handle_pellet_checkout_completed(db, {
        "id": row.stripe_checkout_session_id, "payment_status": "paid",
        "amount_total": 108000, "payment_intent": "pi_wt",
        "metadata": {"pellet_patient_id": str(p.id), "pellet_kind": "package", "insertions": "3"}})
    db.commit()
    log.append("2. Stripe webhook (paid) -> granted 3 insertion credits")

    status = client.get("/api/pellet-portal/payment/status", headers=h).json()
    assert status["available_insertions"] == 3
    log.append(f"3. payment status: available_insertions={status['available_insertions']}")

    r = client.post(f"/api/pellets/patients/{p.id}/consume-insertion")
    assert r.status_code == 200
    assert r.json()["available_insertions"] == 2
    log.append(f"4. staff drew down 1 on completion -> now {r.json()['available_insertions']} left")

    with capsys.disabled():
        print("\n  -- Pellet payments Phase-2 walk-through (authenticated) --")
        for line in log:
            print("   " + line)
