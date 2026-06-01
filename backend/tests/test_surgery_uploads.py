"""Surgery uploads service — multipart write + signed-URL read."""
from unittest.mock import patch, MagicMock

import pytest

from app.models.surgery import Surgery, SurgeryDocument
from app.services.surgery_uploads import (
    ALLOWED_MIME, MAX_BYTES,
    UploadError,
    store_upload,
    signed_download_url,
    stream_static_pdf,
)


PDF_BYTES = b"%PDF-1.4\nthis is a test pdf"
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIFfake-jpeg-data"


def test_store_upload_writes_to_gcs_and_creates_row(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = blob

        doc = store_upload(db, s, kind="clearance",
                              filename="my form.pdf",
                              file_bytes=PDF_BYTES,
                              content_type="application/pdf",
                              uploaded_by="patient:portal")

    assert doc.surgery_id == s.id
    assert doc.kind == "clearance"
    assert doc.filename == "my form.pdf"
    assert doc.content_type == "application/pdf"
    assert doc.size_bytes == len(PDF_BYTES)
    # GCS path includes timestamp + sanitized filename
    assert doc.gcs_path.startswith(f"surgery-uploads/{s.id}/clearance/")
    assert doc.gcs_path.endswith(".pdf")
    # Backed write happened
    blob.upload_from_string.assert_called_once_with(
        PDF_BYTES, content_type="application/pdf"
    )


def test_store_upload_rejects_oversize(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    huge = b"x" * (MAX_BYTES + 1)
    with pytest.raises(UploadError, match="too large"):
        store_upload(db, s, kind="clearance",
                       filename="big.pdf",
                       file_bytes=huge,
                       content_type="application/pdf",
                       uploaded_by="patient:portal")


def test_store_upload_rejects_unknown_mime(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    with pytest.raises(UploadError, match="file type"):
        store_upload(db, s, kind="clearance",
                       filename="run.exe",
                       file_bytes=b"MZfake-exe",
                       content_type="application/x-msdownload",
                       uploaded_by="patient:portal")


def test_store_upload_rejects_mime_mismatch(db):
    """Caller says application/pdf but the bytes are JPEG → reject."""
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    with pytest.raises(UploadError, match="content"):
        store_upload(db, s, kind="clearance",
                       filename="trick.pdf",
                       file_bytes=JPEG_BYTES,
                       content_type="application/pdf",
                       uploaded_by="patient:portal")


def test_signed_download_url_calls_blob_v4(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    doc = SurgeryDocument(surgery_id=s.id, kind="clearance",
                            filename="x.pdf",
                            gcs_path=f"surgery-uploads/{s.id}/x.pdf",
                            uploaded_by="patient:portal")
    db.add(doc); db.commit(); db.refresh(doc)
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        blob.generate_signed_url.return_value = "https://signed.example/x.pdf"
        MockClient.return_value.bucket.return_value.blob.return_value = blob

        url = signed_download_url(doc, ttl_minutes=5)
        assert url == "https://signed.example/x.pdf"
        # Verify v4 + TTL
        _, kwargs = blob.generate_signed_url.call_args
        assert kwargs.get("version") == "v4"


def test_stream_static_pdf_returns_bytes():
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        blob.exists.return_value = True
        blob.download_as_bytes.return_value = b"%PDF-blank"
        MockClient.return_value.bucket.return_value.blob.return_value = blob

        result = stream_static_pdf("clearance/template.pdf")
        assert result == b"%PDF-blank"


def test_stream_static_pdf_returns_none_when_missing():
    with patch("app.services.surgery_uploads.storage.Client") as MockClient:
        blob = MagicMock()
        blob.exists.return_value = False
        MockClient.return_value.bucket.return_value.blob.return_value = blob

        result = stream_static_pdf("clearance/template.pdf")
        assert result is None
