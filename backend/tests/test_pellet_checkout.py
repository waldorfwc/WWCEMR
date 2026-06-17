from datetime import date
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletPayment
from app.services.pellet import portal_auth
from app.services.pellet import payments as pay


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      patient_email="j@x.com")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


class _FakeSession:
    id = "cs_test_1"; url = "https://stripe.test/cs_test_1"


def _mock_stripe(monkeypatch):
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_get_or_create_pellet_customer", lambda db, p: "cus_1")
    monkeypatch.setattr(pay, "_create_checkout_session_obj", lambda **kw: _FakeSession())


def test_options_lists_prices(client, db, auth):
    _p, h = auth
    body = client.get("/api/pellet-portal/payment/options", headers=h).json()
    assert body["insertion_price"] == 400.0
    assert body["enable_single"] is True
    assert any(t["count"] == 3 for t in body["package_tiers"])


def test_single_checkout_creates_payment(client, db, auth, monkeypatch):
    _mock_stripe(monkeypatch)
    p, h = auth
    r = client.post("/api/pellet-portal/payment/single", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"] == "https://stripe.test/cs_test_1"
    row = db.query(PelletPayment).filter(PelletPayment.pellet_patient_id == p.id).first()
    assert row.kind == "single" and row.insertions_purchased == 1 and row.status == "requested"


def test_package_checkout_uses_discount(client, db, auth, monkeypatch):
    _mock_stripe(monkeypatch)
    p, h = auth
    r = client.post("/api/pellet-portal/payment/package", json={"count": 3}, headers=h)
    assert r.status_code == 200, r.text
    row = (db.query(PelletPayment)
             .filter(PelletPayment.pellet_patient_id == p.id,
                     PelletPayment.kind == "package").first())
    assert row.insertions_purchased == 3
    assert float(row.amount) == 1080.0


def test_package_requires_two(client, db, auth, monkeypatch):
    _mock_stripe(monkeypatch)
    _p, h = auth
    r = client.post("/api/pellet-portal/payment/package", json={"count": 1}, headers=h)
    assert r.status_code == 422
