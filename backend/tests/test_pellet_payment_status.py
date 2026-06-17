from datetime import date
from decimal import Decimal
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_payment import PelletInsertionCredit, PelletSubscription
from app.services.pellet import portal_auth


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


def test_payment_status_reports_balance(client, db, auth):
    p, h = auth
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=2, source="package"))
    db.add(PelletSubscription(pellet_patient_id=p.id, monthly_amount=Decimal("100"),
                              accrued_credit=Decimal("150"), status="active"))
    db.commit()
    body = client.get("/api/pellet-portal/payment/status", headers=h).json()
    assert body["credit_balance"] == 2
    assert body["available_insertions"] == 2
    assert body["subscription"]["accrued_credit"] == 150.0
    assert body["subscription"]["status"] == "active"


def test_dashboard_includes_payment_summary(client, db, auth):
    p, h = auth
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    db.commit()
    dash = client.get("/api/pellet-portal/dashboard", headers=h).json()
    assert dash["payment"]["available_insertions"] == 1


def test_staff_drawdown_consumes_credit(client, db, auth):
    p, _h = auth
    db.add(PelletInsertionCredit(pellet_patient_id=p.id, delta=1, source="single"))
    db.commit()
    r = client.post(f"/api/pellets/patients/{p.id}/consume-insertion")
    assert r.status_code == 200, r.text
    from app.services.pellet import payments as pay
    assert pay.credit_balance(db, p) == 0


def test_staff_drawdown_409_when_no_credit(client, db, auth):
    p, _h = auth
    r = client.post(f"/api/pellets/patients/{p.id}/consume-insertion")
    assert r.status_code == 409
