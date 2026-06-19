"""BoldSign webhook receiver — signature verification + status apply."""
import hashlib
import hmac
import json
from unittest.mock import patch

from app.models.surgery import (
    Surgery, ConsentTemplate, SurgeryConsentEnvelope,
)


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _seed_envelope(db):
    s = Surgery(
        chart_number="1", patient_name="Jane Doe", email="jane@example.com",
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="confirmed",
        procedures=[{"name": "Robotic hyst"}],
    )
    db.add(s); db.flush()
    t = ConsentTemplate(
        name="Robotic hyst consent",
        boldsign_template_id="bs_tmpl_1",
        procedure_match=["Robotic"],
    )
    db.add(t); db.flush()
    row = SurgeryConsentEnvelope(
        surgery_id=s.id, template_id=t.id,
        boldsign_envelope_id="bs_doc_42",
        status="sent",
    )
    db.add(row); db.commit(); db.refresh(row)
    return s, t, row


def test_webhook_rejects_bad_signature(client, monkeypatch):
    monkeypatch.setenv("BOLDSIGN_WEBHOOK_SECRET", "secret123")
    body = b'{"event":"Completed","data":{"documentId":"bs_doc_42","status":"Completed"}}'
    resp = client.post("/api/boldsign/webhook",
                        content=body,
                        headers={"x-boldsign-signature": "totally-wrong"})
    assert resp.status_code == 400


def test_webhook_setup_mode_when_secret_missing(client, monkeypatch):
    """When BOLDSIGN_WEBHOOK_SECRET is unset, the endpoint is fail-CLOSED
    (503) unless the operator opts in with BOLDSIGN_WEBHOOK_ALLOW_UNSIGNED.
    With that opt-in flag set, it enters setup mode and returns 200 to let
    BoldSign's Verify button pass during initial dashboard configuration."""
    monkeypatch.delenv("BOLDSIGN_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("BOLDSIGN_WEBHOOK_ALLOW_UNSIGNED", "true")
    body = b'{"event":"Completed"}'
    sig = _sign(body, "some-secret")
    resp = client.post("/api/boldsign/webhook",
                        content=body,
                        headers={"x-boldsign-signature": sig})
    assert resp.status_code == 200
    body_json = resp.json()
    assert body_json["received"] is True
    assert body_json["reason"] == "setup mode"


def test_webhook_applies_completed_status(client, db, monkeypatch):
    monkeypatch.setenv("BOLDSIGN_WEBHOOK_SECRET", "secret123")
    s, t, row = _seed_envelope(db)

    body = json.dumps({
        "event": "Completed",
        "data": {"documentId": "bs_doc_42", "status": "Completed"},
    }).encode("utf-8")
    sig = _sign(body, "secret123")

    # The webhook also triggers reconcile_surgery_consent which would
    # hit BoldSign's HTTP API — mock that out so the test stays offline.
    with patch("app.services.boldsign_envelopes.reconcile_surgery_consent"):
        resp = client.post("/api/boldsign/webhook",
                            content=body,
                            headers={"x-boldsign-signature": sig})

    assert resp.status_code == 200, resp.text
    body_json = resp.json()
    assert body_json["applied"] is True
    assert body_json["after_status"] in ("signed", "completed")
    db.refresh(row)
    assert row.status in ("signed", "completed")


def test_webhook_ignores_unknown_document_id(client, db, monkeypatch):
    monkeypatch.setenv("BOLDSIGN_WEBHOOK_SECRET", "secret123")
    body = json.dumps({
        "event": "Completed",
        "data": {"documentId": "bs_doc_unknown", "status": "Completed"},
    }).encode("utf-8")
    sig = _sign(body, "secret123")

    resp = client.post("/api/boldsign/webhook",
                        content=body,
                        headers={"x-boldsign-signature": sig})
    assert resp.status_code == 200
    body_json = resp.json()
    assert body_json["applied"] is False


def test_webhook_handles_missing_document_id(client, monkeypatch):
    monkeypatch.setenv("BOLDSIGN_WEBHOOK_SECRET", "secret123")
    body = b'{"event":"Completed","data":{"status":"Completed"}}'
    sig = _sign(body, "secret123")
    resp = client.post("/api/boldsign/webhook",
                        content=body,
                        headers={"x-boldsign-signature": sig})
    assert resp.status_code == 200
    assert resp.json()["applied"] is False
