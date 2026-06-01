"""Active-AR claim documents — uploads + downloads via storage adapter."""
from unittest.mock import patch
from datetime import datetime


def _seed_claim(db):
    from app.models.active_ar import ActiveClaim
    c = ActiveClaim(
        claim_number="C-T4",
        patient_external_id="9001",
        patient_name="Pat",
        dos=datetime.utcnow().date(),
        insurance_company="Test Payer",
        insurance_priority="Primary",
        claim_amount=100, paid_amount=0, insurance_balance=100,
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_claim_document_upload_stores_gcs_key(client, db):
    c = _seed_claim(db)
    with patch("app.routers.active_ar.save_blob",
                return_value="active-ar-docs/abc.pdf") as mock:
        r = client.post(
            f"/api/active-ar/claims/{c.id}/documents?document_type=EOB",
            files={"file": ("eob.pdf", b"%PDF-1.4 x",
                              "application/pdf")},
        )
    assert r.status_code == 200, r.text
    from app.models.active_ar import ActiveClaimDocument
    doc = (db.query(ActiveClaimDocument)
             .filter(ActiveClaimDocument.active_claim_id == c.id).first())
    assert doc.file_path == "active-ar-docs/abc.pdf"
    _, kwargs = mock.call_args
    assert kwargs["prefix"] == "active-ar-docs"


def test_claim_document_download_via_serve_blob(client, db):
    c = _seed_claim(db)
    from app.models.active_ar import ActiveClaimDocument
    doc = ActiveClaimDocument(
        active_claim_id=c.id, document_type="EOB", filename="eob.pdf",
        content_type="application/pdf", file_size=12,
        file_path="active-ar-docs/key.pdf",
        uploaded_by="tester@example.com",
    )
    db.add(doc); db.commit(); db.refresh(doc)
    from fastapi.responses import Response
    with patch("app.routers.active_ar.serve_blob",
                return_value=Response(content=b"%PDF-1.4 ok",
                                          media_type="application/pdf")) as mock:
        r = client.get(f"/api/active-ar/claims/{c.id}/documents/{doc.id}/download")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs["gcs_object"] == "active-ar-docs/key.pdf"
    assert kwargs["local_path"] is None


def test_claim_document_download_legacy_path_returns_410(client, db):
    c = _seed_claim(db)
    from app.models.active_ar import ActiveClaimDocument
    doc = ActiveClaimDocument(
        active_claim_id=c.id, document_type="EOB", filename="eob.pdf",
        content_type="application/pdf", file_size=12,
        file_path="/var/data/old/eob.pdf",
        uploaded_by="tester@example.com",
    )
    db.add(doc); db.commit(); db.refresh(doc)
    r = client.get(f"/api/active-ar/claims/{c.id}/documents/{doc.id}/download")
    assert r.status_code == 410
