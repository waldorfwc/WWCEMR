"""Staff can view a signed surgery consent PDF from the consents card.

GET /surgery/{id}/consent/envelopes/{env_id}/document streams the signed PDF
(inline) for signed/completed envelopes; 409 before signing, 404 for a bad id.
"""
import app.services.boldsign_envelopes as bs
from app.models.surgery import (ConsentTemplate, Surgery, SurgeryConsentEnvelope)


def _seed(db, *, status="signed", boldsign_id="bs-doc-1"):
    t = ConsentTemplate(name="Hysterectomy Consent")
    db.add(t); db.flush()
    s = Surgery(chart_number="1", patient_name="Pat", status="confirmed")
    db.add(s); db.flush()
    env = SurgeryConsentEnvelope(surgery_id=s.id, template_id=t.id,
                                 status=status, boldsign_envelope_id=boldsign_id)
    db.add(env); db.commit(); db.refresh(env)
    return s, env


def test_view_signed_consent_returns_pdf(client, db, monkeypatch):
    s, env = _seed(db, status="signed")
    monkeypatch.setattr(bs, "download_signed_pdf", lambda eid: b"%PDF-1.4 fake")
    r = client.get(f"/api/surgery/{s.id}/consent/envelopes/{env.id}/document")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert "inline" in r.headers["content-disposition"]
    assert "Hysterectomy_Consent" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF")


def test_view_unsigned_consent_409(client, db, monkeypatch):
    s, env = _seed(db, status="sent")
    monkeypatch.setattr(bs, "download_signed_pdf", lambda eid: b"x")
    r = client.get(f"/api/surgery/{s.id}/consent/envelopes/{env.id}/document")
    assert r.status_code == 409
    assert "signed" in r.json()["detail"].lower()


def test_view_missing_envelope_404(client, db):
    s, _ = _seed(db)
    r = client.get(
        f"/api/surgery/{s.id}/consent/envelopes/00000000-0000-0000-0000-000000000000/document")
    assert r.status_code == 404


def test_view_non_boldsign_envelope_409(client, db):
    s, env = _seed(db, status="signed", boldsign_id=None)
    r = client.get(f"/api/surgery/{s.id}/consent/envelopes/{env.id}/document")
    assert r.status_code == 409
