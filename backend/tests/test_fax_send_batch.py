"""Tests for POST /api/fax/send-batch."""
import pytest
from app.models.document import PatientDocument
from app.models.patient_directory import PatientDirectory
from app.models.fax_log import FaxLog


def _fake_send_fax_ok(to_number, file_path, cover_page_text=None, patient_name=None):
    return {"success": True, "message_id": "rc-msg-123", "status": "Sent",
            "to": to_number, "pages": 1, "error": None}


def _fake_send_fax_fail(to_number, file_path, cover_page_text=None, patient_name=None):
    return {"success": False, "message_id": None, "status": "Failed",
            "to": to_number, "pages": 0, "error": "Invalid fax number"}


def _seed_doc(db, tmp_path, chart_number="12345", name="Adams, Pamella"):
    """Seed a PatientDocument whose file_path points at a writable temp PDF."""
    pdf_path = tmp_path / f"{chart_number}-doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%fake content\n%%EOF")
    doc = PatientDocument(
        chart_number=chart_number, doc_type="insurance_card",
        doc_id="D1", filename=pdf_path.name, file_path=str(pdf_path),
    )
    patient = PatientDirectory(chart_number=chart_number, patient_name=name)
    db.merge(patient)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_send_batch_separate_one_doc(client, db, tmp_path, monkeypatch):
    doc = _seed_doc(db, tmp_path)
    monkeypatch.setattr("app.routers.fax_batch.send_fax", _fake_send_fax_ok)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "12345",
        "doc_ids": [str(doc.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "separate",
        "cover_text": "test",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["faxes"]) == 1
    assert body["faxes"][0]["status"] == "sent"
    assert body["faxes"][0]["ringcentral_message_id"] == "rc-msg-123"

    logs = db.query(FaxLog).all()
    assert len(logs) == 1
    assert logs[0].chart_number == "12345"
    assert logs[0].status.value == "sent"
    assert logs[0].dest_fax == "2402522141"
    assert logs[0].sent_by == "tester@waldorfwomenscare.com"


def test_send_batch_separate_multiple_docs_creates_multiple_fax_logs(client, db, tmp_path, monkeypatch):
    doc_a = _seed_doc(db, tmp_path, chart_number="11111")
    doc_b = _seed_doc(db, tmp_path, chart_number="11111")
    monkeypatch.setattr("app.routers.fax_batch.send_fax", _fake_send_fax_ok)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "11111",
        "doc_ids": [str(doc_a.id), str(doc_b.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "separate",
    })
    assert r.status_code == 200
    assert len(r.json()["faxes"]) == 2
    assert db.query(FaxLog).count() == 2


def test_send_batch_per_fax_failure_does_not_abort_batch(client, db, tmp_path, monkeypatch):
    doc_a = _seed_doc(db, tmp_path, chart_number="22222")
    doc_b = _seed_doc(db, tmp_path, chart_number="22222")

    # First call succeeds, second fails
    calls = {"n": 0}
    def mock(*args, **kwargs):
        calls["n"] += 1
        return _fake_send_fax_ok(*args, **kwargs) if calls["n"] == 1 else _fake_send_fax_fail(*args, **kwargs)

    monkeypatch.setattr("app.routers.fax_batch.send_fax", mock)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "22222",
        "doc_ids": [str(doc_a.id), str(doc_b.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "separate",
    })
    assert r.status_code == 200
    faxes = r.json()["faxes"]
    statuses = {f["status"] for f in faxes}
    assert statuses == {"sent", "failed"}

    all_logs = db.query(FaxLog).all()
    assert len(all_logs) == 2
    failed = [l for l in all_logs if l.status.value == "failed"]
    assert len(failed) == 1
    assert failed[0].error == "Invalid fax number"


def test_send_batch_rejects_missing_doc(client, db, monkeypatch):
    monkeypatch.setattr("app.routers.fax_batch.send_fax", _fake_send_fax_ok)
    r = client.post("/api/fax/send-batch", json={
        "chart_number": "99999",
        "doc_ids": ["00000000-0000-0000-0000-000000000000"],
        "dest_fax": "2402522141",
        "grouping_mode": "separate",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["faxes"][0]["status"] == "failed"
    assert "not found" in body["faxes"][0]["error"].lower()


def test_send_batch_validates_payload(client, db):
    # Missing dest_fax
    r = client.post("/api/fax/send-batch", json={
        "chart_number": "12345", "doc_ids": ["x"], "grouping_mode": "separate",
    })
    assert r.status_code == 422

    # Empty doc_ids
    r = client.post("/api/fax/send-batch", json={
        "chart_number": "12345", "doc_ids": [], "dest_fax": "2402522141",
        "grouping_mode": "separate",
    })
    assert r.status_code == 400


def _write_pdf(path, body=b"%PDF-1.4\n%content\n%%EOF"):
    path.write_bytes(body)
    return path


def test_send_batch_combined_merges_into_one_fax(client, db, tmp_path, monkeypatch):
    doc_a = _seed_doc(db, tmp_path, chart_number="33333")
    doc_b = _seed_doc(db, tmp_path, chart_number="33333")

    calls = []
    def mock(to_number, file_path, cover_page_text=None, patient_name=None):
        calls.append(file_path)
        return {"success": True, "message_id": f"msg-{len(calls)}",
                "status": "Sent", "to": to_number, "pages": 2, "error": None}
    monkeypatch.setattr("app.routers.fax_batch.send_fax", mock)

    # Make the seeded docs valid single-page PDFs so pypdf can open them.
    # Use a minimal-but-valid one-page PDF for both.
    from pypdf import PdfWriter
    for d in (doc_a, doc_b):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with open(d.file_path, "wb") as f:
            writer.write(f)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "33333",
        "doc_ids": [str(doc_a.id), str(doc_b.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "combined",
    })
    assert r.status_code == 200, r.text
    faxes = r.json()["faxes"]
    assert len(faxes) == 1
    assert faxes[0]["status"] == "sent"
    assert set(faxes[0]["doc_ids"]) == {str(doc_a.id), str(doc_b.id)}
    # send_fax called exactly once with the merged PDF path
    assert len(calls) == 1
    assert calls[0].endswith(".pdf")


def test_send_batch_combined_reports_single_failure(client, db, tmp_path, monkeypatch):
    doc_a = _seed_doc(db, tmp_path, chart_number="44444")
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with open(doc_a.file_path, "wb") as f:
        writer.write(f)

    monkeypatch.setattr("app.routers.fax_batch.send_fax", _fake_send_fax_fail)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "44444",
        "doc_ids": [str(doc_a.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "combined",
    })
    assert r.status_code == 200
    assert r.json()["faxes"][0]["status"] == "failed"


def _seed_doc_type(db, tmp_path, chart_number, doc_type, idx=0):
    pdf_path = tmp_path / f"{chart_number}-{doc_type}-{idx}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%content\n%%EOF")
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with open(pdf_path, "wb") as f:
        writer.write(f)
    doc = PatientDocument(
        chart_number=chart_number, doc_type=doc_type,
        doc_id=f"D{idx}", filename=pdf_path.name, file_path=str(pdf_path),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def test_send_batch_by_type_groups_docs(client, db, tmp_path, monkeypatch):
    db.merge(PatientDirectory(chart_number="55555", patient_name="Nguyen, Mai"))
    db.commit()

    card_a = _seed_doc_type(db, tmp_path, "55555", "insurance_card", 0)
    card_b = _seed_doc_type(db, tmp_path, "55555", "insurance_card", 1)
    note_a = _seed_doc_type(db, tmp_path, "55555", "office_visit_note", 0)

    calls = []
    def mock(to_number, file_path, cover_page_text=None, patient_name=None):
        calls.append((file_path, cover_page_text))
        return {"success": True, "message_id": f"msg-{len(calls)}",
                "status": "Sent", "to": to_number, "pages": 1, "error": None}
    monkeypatch.setattr("app.routers.fax_batch.send_fax", mock)

    r = client.post("/api/fax/send-batch", json={
        "chart_number": "55555",
        "doc_ids": [str(card_a.id), str(card_b.id), str(note_a.id)],
        "dest_fax": "2402522141",
        "grouping_mode": "by_type",
    })
    assert r.status_code == 200, r.text
    faxes = r.json()["faxes"]
    assert len(faxes) == 2  # one per doc_type
    # Each group's doc_ids are the ones matching its type
    sizes = sorted(len(f["doc_ids"]) for f in faxes)
    assert sizes == [1, 2]
    assert len(calls) == 2
