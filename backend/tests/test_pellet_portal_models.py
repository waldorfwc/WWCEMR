from datetime import date, timedelta
from app.utils.dt import now_utc_naive
from app.models.pellet import PelletPatient
from app.models.pellet_portal import (
    PelletConsent, PelletActivity, PelletPortalAuthAttempt, PelletPortalUpload)


def _patient(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234")
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_consent_validity_window(db):
    p = _patient(db)
    signed = now_utc_naive()
    c = PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="env-1",
                      status="signed", signed_at=signed,
                      expires_at=signed + timedelta(days=365))
    db.add(c); db.commit(); db.refresh(c)
    assert c.is_valid is True
    c.expires_at = signed - timedelta(days=1)
    assert c.is_valid is False


def test_activity_row(db):
    p = _patient(db)
    a = PelletActivity(pellet_patient_id=p.id, kind="mammo_uploaded",
                       summary="Uploaded mammogram", actor="patient")
    db.add(a); db.commit(); db.refresh(a)
    assert a.read_at is None and a.actor == "patient"


def test_auth_attempt_row(db):
    p = _patient(db)
    att = PelletPortalAuthAttempt(pellet_patient_id=p.id, challenge_token="ct",
                                  code_hash="h", purpose="login")
    db.add(att); db.commit(); db.refresh(att)
    assert att.consumed_at is None


def test_portal_upload_row(db):
    p = _patient(db)
    u = PelletPortalUpload(pellet_patient_id=p.id, kind="mammo",
                           filename="m.pdf", storage_path="pellet-mammo/x.pdf")
    db.add(u); db.commit(); db.refresh(u)
    assert u.kind == "mammo"
