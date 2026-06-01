"""Patient portal P5 schema."""
from datetime import datetime

from app.models.surgery import Surgery, SurgeryDocument


def test_surgery_document_persists(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    doc = SurgeryDocument(
        surgery_id=s.id,
        kind="clearance",
        filename="clearance.pdf",
        gcs_path=f"surgery-uploads/{s.id}/clearance/2026-06-01_clearance.pdf",
        content_type="application/pdf",
        size_bytes=12345,
        uploaded_by="patient:portal",
    )
    db.add(doc); db.commit(); db.refresh(doc)
    assert doc.id is not None
    assert doc.uploaded_at is not None  # default fired
    assert doc.kind == "clearance"


def test_surgery_documents_relationship(db):
    s = Surgery(chart_number="2", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    db.add_all([
        SurgeryDocument(surgery_id=s.id, kind="clearance",
                          filename="a.pdf",
                          gcs_path=f"surgery-uploads/{s.id}/a.pdf",
                          uploaded_by="patient:portal"),
        SurgeryDocument(surgery_id=s.id, kind="ekg",
                          filename="b.pdf",
                          gcs_path=f"surgery-uploads/{s.id}/b.pdf",
                          uploaded_by="patient:portal"),
    ])
    db.commit(); db.refresh(s)
    assert len(s.documents) == 2
    kinds = {d.kind for d in s.documents}
    assert kinds == {"clearance", "ekg"}
