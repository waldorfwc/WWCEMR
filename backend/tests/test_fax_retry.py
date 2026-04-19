"""POST /api/fax/retry/{fax_log_id} — resend a failed fax, link to original via retry_of."""
from app.models.document import PatientDocument
from app.models.patient_directory import PatientDirectory
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode


def _ok(to_number, file_path, cover_page_text=None, patient_name=None):
    return {"success": True, "message_id": "retry-msg-1", "status": "Sent",
            "to": to_number, "pages": 1, "error": None}


def test_retry_resends_failed_fax_and_links(client, db, tmp_path, monkeypatch):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%r\n%%EOF")
    doc = PatientDocument(
        chart_number="77777", doc_type="insurance_card",
        doc_id="D7", filename="r.pdf", file_path=str(pdf),
    )
    db.add(doc)
    db.merge(PatientDirectory(chart_number="77777", patient_name="Retry, Case"))
    db.commit()
    db.refresh(doc)

    failed = FaxLog(
        chart_number="77777", doc_ids=[str(doc.id)],
        grouping_mode=GroupingMode.SEPARATE, dest_fax="2402522141",
        status=FaxLogStatus.FAILED, error="prev error",
    )
    db.add(failed)
    db.commit()
    db.refresh(failed)

    monkeypatch.setattr("app.routers.fax_batch.send_fax", _ok)
    r = client.post(f"/api/fax/retry/{failed.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["faxes"][0]["status"] == "sent"

    all_logs = db.query(FaxLog).order_by(FaxLog.sent_at).all()
    assert len(all_logs) == 2
    new_log = [l for l in all_logs if l.status == FaxLogStatus.SENT][0]
    assert str(new_log.retry_of) == str(failed.id)


def test_retry_404_on_missing(client, db):
    r = client.post("/api/fax/retry/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
