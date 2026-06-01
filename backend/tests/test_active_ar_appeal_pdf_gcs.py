"""Appeal letter PDFs — generated to GCS via storage adapter."""
from unittest.mock import patch
from datetime import datetime


def _seed_appeal(client, db):
    """Helper: create a claim + an AppealLetter ready for PDF generation."""
    from app.models.active_ar import ActiveClaim
    from app.models.appeal_letters import AppealLetter
    c = ActiveClaim(
        claim_number="C-AP", patient_external_id="9002",
        patient_name="Pat", dos=datetime.utcnow().date(),
        insurance_company="Test", insurance_priority="Primary",
        claim_amount=100, paid_amount=0, insurance_balance=100,
    )
    db.add(c); db.commit(); db.refresh(c)
    a = AppealLetter(
        active_claim_id=c.id, level=1, template_type="medical_necessity",
        subject="Subj", body="Body", status="draft",
    )
    db.add(a); db.commit(); db.refresh(a)
    return c, a


def test_generate_appeal_pdf_stores_gcs_keys_in_both_tables(client, db):
    c, a = _seed_appeal(client, db)
    with patch("app.routers.active_ar.render_pdf",
                return_value=b"%PDF-1.4 appeal") as mock_render, \
         patch("app.routers.active_ar.save_blob",
                return_value="appeal-letters/letter.pdf") as mock_save:
        r = client.post(f"/api/active-ar/appeals/{a.id}/generate-pdf")
    assert r.status_code == 200, r.text
    db.refresh(a)
    assert a.pdf_path == "appeal-letters/letter.pdf"
    assert a.status == "generated"
    # render_pdf called WITHOUT output_path (or with None)
    _, kwargs = mock_render.call_args
    assert kwargs.get("output_path") is None or "output_path" not in kwargs
    _, save_kwargs = mock_save.call_args
    assert save_kwargs["prefix"] == "appeal-letters"
    # Also created the auto-attached ActiveClaimDocument
    from app.models.active_ar import ActiveClaimDocument
    doc = (db.query(ActiveClaimDocument)
             .filter(ActiveClaimDocument.active_claim_id == c.id,
                       ActiveClaimDocument.document_type == "Appeal").first())
    assert doc is not None
    assert doc.file_path == "appeal-letters/letter.pdf"


def test_download_appeal_pdf_via_serve_blob(client, db):
    c, a = _seed_appeal(client, db)
    a.pdf_path = "appeal-letters/exists.pdf"
    a.status = "generated"
    db.commit()
    from fastapi.responses import Response
    with patch("app.routers.active_ar.serve_blob",
                return_value=Response(content=b"%PDF-1.4 ok",
                                          media_type="application/pdf")) as mock:
        r = client.get(f"/api/active-ar/appeals/{a.id}/pdf")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs["gcs_object"] == "appeal-letters/exists.pdf"


def test_download_appeal_pdf_legacy_path_returns_410(client, db):
    c, a = _seed_appeal(client, db)
    a.pdf_path = "/var/data/appeals/old.pdf"
    a.status = "generated"
    db.commit()
    r = client.get(f"/api/active-ar/appeals/{a.id}/pdf")
    assert r.status_code == 410
