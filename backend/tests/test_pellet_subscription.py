from datetime import date
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletSubscription
from app.models.pellet_config import PelletConfig
from app.services.pellet import portal_auth
from app.services.pellet import payments as pay


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      patient_email="j@x.com")
    db.add(p); db.commit(); db.refresh(p)
    db.add(PelletConfig(key="subscription_monthly_amount", value=100.0))
    db.commit()
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


class _FakeSub:
    id = "sub_test_1"
    status = "active"


def test_subscribe_creates_subscription_row(client, db, auth, monkeypatch):
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_get_or_create_pellet_customer", lambda db, p: "cus_1")
    monkeypatch.setattr(pay, "_create_stripe_subscription",
                        lambda **kw: (_FakeSub(), "price_1"))
    p, h = auth
    r = client.post("/api/pellet-portal/payment/subscribe", headers=h)
    assert r.status_code == 200, r.text
    sub = db.query(PelletSubscription).filter(
        PelletSubscription.pellet_patient_id == p.id).first()
    assert sub.stripe_subscription_id == "sub_test_1"
    assert float(sub.monthly_amount) == 100.0
    assert sub.status == "active"


def test_subscribe_already_subscribed(client, db, auth, monkeypatch):
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_get_or_create_pellet_customer", lambda db, p: "cus_1")
    monkeypatch.setattr(pay, "_create_stripe_subscription",
                        lambda **kw: (_FakeSub(), "price_1"))
    p, h = auth
    assert client.post("/api/pellet-portal/payment/subscribe", headers=h).status_code == 200
    r2 = client.post("/api/pellet-portal/payment/subscribe", headers=h)
    assert r2.status_code == 409


def test_subscribe_no_amount_configured(client, db, auth):
    from app.models.pellet_config import PelletConfig
    db.query(PelletConfig).filter(PelletConfig.key == "subscription_monthly_amount").delete()
    db.commit()
    _p, h = auth
    r = client.post("/api/pellet-portal/payment/subscribe", headers=h)
    assert r.status_code == 409
