"""Backward-compat: old /fax/send must still work and also create a FaxLog row."""
from app.models.document import PatientDocument
from app.models.patient_directory import PatientDirectory
from app.models.fax_log import FaxLog


def _ok(to_number, file_path, cover_page_text=None, patient_name=None):
    return {"success": True, "message_id": "rc-compat-1", "status": "Sent",
            "to": to_number, "pages": 1, "error": None}


def test_legacy_fax_send_creates_fax_log(client, db, tmp_path, monkeypatch):
    pdf = tmp_path / "c.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%c\n%%EOF")
    doc = PatientDocument(
        chart_number="66666", doc_type="insurance_card",
        doc_id="D9", filename="c.pdf", file_path=str(pdf),
    )
    db.add(doc)
    db.merge(PatientDirectory(chart_number="66666", patient_name="Compat, Case"))
    db.commit()
    db.refresh(doc)

    monkeypatch.setattr("app.routers.fax_batch.send_fax", _ok)

    r = client.post("/api/fax/send", json={
        "fax_number": "2402522141",
        "doc_type": "document",
        "doc_id": str(doc.id),
        "cover_text": "test",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # Legacy response shape preserved
    assert body["success"] is True
    assert body["message_id"] == "rc-compat-1"

    # But a FaxLog row was also written
    logs = db.query(FaxLog).all()
    assert len(logs) == 1
    assert logs[0].chart_number == "66666"
    assert logs[0].status.value == "sent"
    assert logs[0].sent_by == "tester@waldorfwomenscare.com"
