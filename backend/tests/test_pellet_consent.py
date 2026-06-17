from datetime import date, timedelta
import pytest
from app.models.pellet import PelletPatient
from app.models.pellet_portal import PelletConsent
from app.services.pellet import portal_auth
from app.utils.dt import now_utc_naive


@pytest.fixture
def auth(db):
    p = PelletPatient(patient_name="Doe, Jane", chart_number="MRN1",
                      patient_dob=date(1980, 5, 1), patient_phone="3015551234",
                      patient_email="j@x.com")
    db.add(p); db.commit(); db.refresh(p)
    return p, {"Authorization": f"Bearer {portal_auth.issue_portal_token(p)}"}


def test_consent_send_creates_sent_row(client, db, auth, monkeypatch):
    import app.services.boldsign_envelopes as be
    monkeypatch.setattr(be, "_create_pellet_envelope", lambda p, tid: "env-123")
    from app.models.pellet_config import PelletConfig
    db.add(PelletConfig(key="consent_template_id", value="tmpl-1")); db.commit()
    p, h = auth
    r = client.post("/api/pellet-portal/consent", headers=h)
    assert r.status_code == 200, r.text
    row = db.query(PelletConsent).filter(PelletConsent.pellet_patient_id == p.id).first()
    assert row.status == "sent" and row.boldsign_envelope_id == "env-123"


def test_consent_no_template_configured(client, db, auth):
    _p, h = auth
    r = client.post("/api/pellet-portal/consent", headers=h)
    assert r.status_code == 409


def test_consent_reuse_within_year(client, db, auth):
    p, h = auth
    db.add(PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="old",
                         status="signed", signed_at=now_utc_naive(),
                         expires_at=now_utc_naive() + timedelta(days=200)))
    db.commit()
    r = client.post("/api/pellet-portal/consent", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "already_valid"


def test_webhook_signed_sets_expiry(client, db, auth):
    p, _h = auth
    c = PelletConsent(pellet_patient_id=p.id, boldsign_envelope_id="env-9", status="sent")
    db.add(c); db.commit()
    from app.routers.boldsign import _apply_pellet_signed
    _apply_pellet_signed(db, "env-9")
    db.refresh(c)
    assert c.status == "signed"
    assert c.signed_at is not None
    assert abs((c.expires_at - c.signed_at) - timedelta(days=365)) < timedelta(seconds=5)
    assert c.is_valid is True
