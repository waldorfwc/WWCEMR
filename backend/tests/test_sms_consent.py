"""SMS consent — coordinator + patient toggles (J4)."""
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.surgery import Surgery


def _make_surgery(db, **kw):
    s = Surgery(
        chart_number="1", patient_name="Pat",
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="confirmed", **kw,
    )
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_coordinator_patch_consent_stamps_timestamp(client, db):
    s = _make_surgery(db)
    resp = client.patch(f"/api/surgery/{s.id}", json={
        "sms_consent": True,
        "cell_phone":  "+15555550100",
    })
    assert resp.status_code == 200
    db.refresh(s)
    assert s.sms_consent is True
    assert s.sms_consented_at is not None
    assert s.sms_consented_by == "tester@waldorfwomenscare.com"
    assert s.cell_phone == "+15555550100"


def test_coordinator_patch_consent_off_clears_timestamp(client, db):
    s = _make_surgery(db, sms_consent=True,
                       sms_consented_at=datetime.utcnow(),
                       sms_consented_by="x@y.com")
    resp = client.patch(f"/api/surgery/{s.id}", json={"sms_consent": False})
    assert resp.status_code == 200
    db.refresh(s)
    assert s.sms_consent is False
    assert s.sms_consented_at is None
    assert s.sms_consented_by is None


def test_patient_consent_endpoint(client, db):
    s = _make_surgery(db)
    resp = client.post(f"/api/p/surgery/{s.id}/sms-consent", json={
        "sms_consent": True,
        "cell_phone":  "+15555550111",
    })
    assert resp.status_code == 200
    db.refresh(s)
    assert s.sms_consent is True
    assert s.sms_consented_by == "patient:self-service"
    assert s.cell_phone == "+15555550111"


def test_patient_consent_endpoint_off(client, db):
    s = _make_surgery(db, sms_consent=True,
                       sms_consented_at=datetime.utcnow(),
                       sms_consented_by="patient:self-service")
    resp = client.post(f"/api/p/surgery/{s.id}/sms-consent", json={
        "sms_consent": False,
    })
    assert resp.status_code == 200
    db.refresh(s)
    assert s.sms_consent is False
    assert s.sms_consented_at is None
