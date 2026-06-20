from datetime import date

from app.models.larc import LarcAssignment, LarcDeviceType
from app.services.larc import portal_auth


def _seed(db, flow="in_stock"):
    dt = LarcDeviceType(name="Mirena", category="larc",
                        default_flow="pharmacy_order", is_active=True)
    db.add(dt); db.commit(); db.refresh(dt)
    a = LarcAssignment(chart_number="P1", patient_name="Doe, J",
                       device_type_id=dt.id, source_flow=flow,
                       status="in_progress", is_active=True,
                       patient_dob=date(1990, 5, 1), patient_cell="240-555-0123",
                       patient_email="p@example.com", patient_responsibility=120)
    db.add(a); db.commit(); db.refresh(a)
    return a


def _auth(a):
    return {"Authorization": f"Bearer {portal_auth.issue_portal_token(a)}"}


def test_dashboard_requires_token(client, db):
    a = _seed(db)
    assert client.get("/api/larc-portal/dashboard").status_code == 401


def test_dashboard_returns_track(client, db):
    a = _seed(db)
    r = client.get("/api/larc-portal/dashboard", headers=_auth(a))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["track"] == "practice_owned"
    assert len(body["steps"]) == 5
    assert body["payment"]["responsibility"] == 120.0


def test_payments_checkout_returns_url(client, db, monkeypatch):
    a = _seed(db)
    monkeypatch.setattr(
        "app.services.stripe_payments.create_larc_checkout",
        lambda db, assignment, amount, **kw: {
            "checkout_url": "https://stripe.test/larc", "payment_id": "p1"})
    r = client.post("/api/larc-portal/payments/checkout", headers=_auth(a))
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"] == "https://stripe.test/larc"
