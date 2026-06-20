"""PHI-access audit on document-view endpoints.

Streaming a patient's signed consent PDF or a boarding-slip file is a PHI
access and must land an audit row just like the send path does. These tests
hit the real view endpoints with the super-admin `client` and assert the
expected AuditLog rows (action + patient_id) were written.
"""
from __future__ import annotations

import app.services.boldsign_envelopes as bs
import app.routers.surgery as surgery_router
from fastapi import Response

from app.models.audit import AuditLog
from app.models.surgery import (
    ConsentTemplate, Surgery, SurgeryConsentEnvelope, SurgeryFile,
)


def _seed_consent(db, *, status="signed", boldsign_id="bs-doc-1"):
    t = ConsentTemplate(name="Hysterectomy Consent")
    db.add(t); db.flush()
    s = Surgery(chart_number="CHT-501", patient_name="Pat", status="confirmed",
                surgery_number="SUR00042")
    db.add(s); db.flush()
    env = SurgeryConsentEnvelope(surgery_id=s.id, template_id=t.id,
                                 status=status, boldsign_envelope_id=boldsign_id)
    db.add(env); db.commit(); db.refresh(env); db.refresh(s)
    return s, env


def _seed_boarding_slip(db):
    s = Surgery(chart_number="CHT-777", patient_name="Boarder", status="confirmed",
                surgery_number="SUR00077")
    db.add(s); db.flush()
    f = SurgeryFile(
        surgery_id=s.id, kind="boarding_slip",
        filename="medstar_CHT777.pdf",
        path="surgery_boarding_slips/medstar_CHT777.pdf",
        mime_type="application/pdf", size_bytes=10,
    )
    db.add(f); db.commit(); db.refresh(f); db.refresh(s)
    return s, f


def test_view_consent_document_writes_phi_audit(client, db, monkeypatch):
    s, env = _seed_consent(db, status="signed")
    monkeypatch.setattr(bs, "download_signed_pdf", lambda eid: b"%PDF-1.4 fake")

    r = client.get(f"/api/surgery/{s.id}/consent/envelopes/{env.id}/document")
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")

    rows = (db.query(AuditLog)
              .filter(AuditLog.action == "PHI_CONSENT_VIEWED").all())
    assert len(rows) == 1
    assert rows[0].patient_id == "CHT-501"
    assert rows[0].resource_type == "surgery"
    assert rows[0].user_id  # actor recorded


def test_download_boarding_slip_file_writes_phi_audit(client, db, monkeypatch):
    s, f = _seed_boarding_slip(db)
    # Don't touch real storage — stub the streaming seam.
    monkeypatch.setattr(
        surgery_router, "serve_blob",
        lambda **kw: Response(content=b"%PDF", media_type="application/pdf"))

    r = client.get(f"/api/surgery/{s.id}/files/{f.id}/download")
    assert r.status_code == 200, r.text

    rows = (db.query(AuditLog)
              .filter(AuditLog.action == "PHI_SURGERY_FILE_VIEWED").all())
    assert len(rows) == 1
    assert rows[0].patient_id == "CHT-777"
    assert rows[0].resource_type == "surgery_file"
    assert "boarding_slip" in (rows[0].description or "")
    assert "medstar_CHT777.pdf" in (rows[0].description or "")
    assert rows[0].user_id
