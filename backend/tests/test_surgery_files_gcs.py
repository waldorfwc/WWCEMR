"""SurgeryFile attachments — uploads + downloads via storage adapter."""
from unittest.mock import patch


def _seed_surgery(db):
    from app.models.surgery import Surgery
    s = Surgery(chart_number="T3", patient_name="Pat", status="new",
                  version_id=1)
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_surgery_file_upload_stores_gcs_key(client, db):
    s = _seed_surgery(db)
    with patch("app.routers.surgery.save_blob",
                return_value="surgery-files/abc.pdf") as mock:
        r = client.post(
            f"/api/surgery/{s.id}/files?kind=prior_auth",
            files={"file": ("auth.pdf", b"%PDF-1.4 x",
                              "application/pdf")},
        )
    assert r.status_code == 201, r.text
    from app.models.surgery import SurgeryFile
    f_row = db.query(SurgeryFile).filter(SurgeryFile.surgery_id == s.id).first()
    assert f_row is not None
    assert f_row.path == "surgery-files/abc.pdf"
    mock.assert_called_once()
    _, kwargs = mock.call_args
    assert kwargs["prefix"] == "surgery-files"
    assert kwargs["filename"] == "auth.pdf"


def test_surgery_file_download_via_serve_blob(client, db):
    s = _seed_surgery(db)
    from app.models.surgery import SurgeryFile
    f_row = SurgeryFile(surgery_id=s.id, kind="op_notes",
                            filename="notes.pdf",
                            path="surgery-files/notes-key.pdf",
                            mime_type="application/pdf",
                            size_bytes=10,
                            uploaded_by="tester@example.com")
    db.add(f_row); db.commit(); db.refresh(f_row)
    from fastapi.responses import Response
    with patch("app.routers.surgery.serve_blob",
                return_value=Response(content=b"%PDF-1.4 ok",
                                          media_type="application/pdf")) as mock:
        r = client.get(f"/api/surgery/{s.id}/files/{f_row.id}/download")
    assert r.status_code == 200
    _, kwargs = mock.call_args
    assert kwargs["gcs_object"] == "surgery-files/notes-key.pdf"
    assert kwargs["local_path"] is None


def test_surgery_file_download_legacy_path_returns_410(client, db):
    s = _seed_surgery(db)
    from app.models.surgery import SurgeryFile
    f_row = SurgeryFile(surgery_id=s.id, kind="op_notes",
                            filename="old.pdf",
                            path="/Users/wwcclaudecode/Documents/wwc-era-project/backend/uploads/surgery_files/old.pdf",
                            mime_type="application/pdf",
                            size_bytes=10,
                            uploaded_by="tester@example.com")
    db.add(f_row); db.commit(); db.refresh(f_row)
    r = client.get(f"/api/surgery/{s.id}/files/{f_row.id}/download")
    assert r.status_code == 410
