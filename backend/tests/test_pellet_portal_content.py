from datetime import date
import pytest
from app.models.pellet import PelletPatient, PelletVisit, PelletVisitDose, PelletDoseType
from app.services.pellet import portal_auth


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


def test_info_returns_config_text(client, db, auth):
    _p, h = auth
    body = client.get("/api/pellet-portal/info", headers=h).json()
    assert "info_text" in body and isinstance(body["info_text"], str) and len(body["info_text"]) > 0


def test_config_roundtrips_portal_info_text(client, db):
    r = client.put("/api/pellets/config", json={"portal_info_text": "## Rules\nBe within 1 year."})
    assert r.status_code == 200, r.text
    assert client.get("/api/pellets/config").json()["portal_info_text"] == "## Rules\nBe within 1 year."


def test_appointments_lists_visits_with_dosage(client, db, auth):
    p, h = auth
    dt = PelletDoseType(hormone="estradiol", dose_mg=12.5, label="Estradiol 12.5mg")
    db.add(dt); db.flush()
    v = PelletVisit(patient_id=p.id, visit_kind="repeat", status="inserted",
                    scheduled_date=date(2026, 5, 1), location="white_plains",
                    provider="Cooke, Aryian, MD")
    db.add(v); db.flush()
    db.add(PelletVisitDose(visit_id=v.id, dose_type_id=dt.id, quantity=2))
    db.commit()
    items = client.get("/api/pellet-portal/appointments", headers=h).json()["items"]
    assert len(items) == 1
    a = items[0]
    assert a["location"] == "white_plains" and a["provider"] == "Cooke, Aryian, MD"
    assert a["status"] == "inserted" and a["scheduled_date"] == "2026-05-01"
    assert a["doses"] == [{"label": "Estradiol 12.5mg", "quantity": 2}]


def test_appointments_empty_for_new_patient(client, db, auth):
    _p, h = auth
    assert client.get("/api/pellet-portal/appointments", headers=h).json()["items"] == []


from decimal import Decimal
from app.models.pellet_payment import PelletPayment
from app.services.pellet import payments as pay


def _paid(db, p, **kw):
    kw.setdefault("amount", Decimal("400.00"))
    row = PelletPayment(pellet_patient_id=p.id,
                        status="paid", requested_by="patient", **kw)
    db.add(row); db.commit(); db.refresh(row)
    return row


def test_receipts_lists_paid(client, db, auth):
    p, h = auth
    _paid(db, p, kind="single", stripe_payment_intent_id="pi_1")
    _paid(db, p, kind="subscription_invoice", stripe_invoice_id="in_1", amount=Decimal("100"))
    db.add(PelletPayment(pellet_patient_id=p.id, kind="single", amount=Decimal("400"),
                         status="requested", requested_by="patient")); db.commit()
    items = client.get("/api/pellet-portal/receipts", headers=h).json()["items"]
    assert len(items) == 2
    assert all(it["status"] == "paid" for it in items)
    assert {it["kind"] for it in items} == {"single", "subscription_invoice"}
    assert all(it["has_receipt"] for it in items)


def test_receipt_url_resolves_stripe(client, db, auth, monkeypatch):
    p, h = auth
    row = _paid(db, p, kind="single", stripe_payment_intent_id="pi_1")

    class _Charge: receipt_url = "https://stripe.test/receipt/abc"
    class _PI: latest_charge = _Charge()
    class _FakeStripe:
        class PaymentIntent:
            @staticmethod
            def retrieve(pid, **kw): return _PI()
    monkeypatch.setattr(pay, "is_configured", lambda: True)
    monkeypatch.setattr(pay, "_client", lambda: _FakeStripe)
    r = client.get(f"/api/pellet-portal/receipts/{row.id}/receipt-url", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://stripe.test/receipt/abc"


def test_receipt_url_404_when_unconfigured(client, db, auth, monkeypatch):
    p, h = auth
    row = _paid(db, p, kind="single", stripe_payment_intent_id="pi_x")
    monkeypatch.setattr(pay, "is_configured", lambda: False)
    assert client.get(f"/api/pellet-portal/receipts/{row.id}/receipt-url", headers=h).status_code == 404


def test_receipt_url_rejects_other_patients_payment(client, db, auth):
    from datetime import date
    _p, h = auth
    other = PelletPatient(patient_name="Other, Pat", chart_number="MRN9",
                          patient_dob=date(1980, 1, 1), patient_phone="3015550000")
    db.add(other); db.flush()
    row = PelletPayment(pellet_patient_id=other.id, kind="single", amount=Decimal("400"),
                        status="paid", requested_by="patient", stripe_payment_intent_id="pi_o")
    db.add(row); db.commit()
    assert client.get(f"/api/pellet-portal/receipts/{row.id}/receipt-url", headers=h).status_code in (403, 404)
