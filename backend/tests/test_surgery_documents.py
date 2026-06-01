"""Surgery documents service — GCS instructions library."""
from unittest.mock import patch, MagicMock

from app.services.surgery_documents import (
    fetch_instructions_pdf,
    INSTRUCTIONS_BUCKET,
)


def test_returns_pdf_bytes_when_object_exists():
    with patch("app.services.surgery_documents.storage.Client") as MockClient:
        blob = MagicMock()
        blob.exists.return_value = True
        blob.download_as_bytes.return_value = b"%PDF-test"
        bucket = MagicMock()
        bucket.blob.return_value = blob
        MockClient.return_value.bucket.return_value = bucket

        result = fetch_instructions_pdf("office_d_and_c", "preop")
        assert result == b"%PDF-test"
        bucket.blob.assert_called_with(
            "surgery-instructions/office_d_and_c/preop.pdf"
        )


def test_returns_none_when_object_missing():
    with patch("app.services.surgery_documents.storage.Client") as MockClient:
        blob = MagicMock()
        blob.exists.return_value = False
        bucket = MagicMock()
        bucket.blob.return_value = blob
        MockClient.return_value.bucket.return_value = bucket

        result = fetch_instructions_pdf("nonexistent_procedure", "preop")
        assert result is None


def test_returns_none_for_invalid_kind():
    """Defensive: caller should validate, but the service shouldn't crash."""
    result = fetch_instructions_pdf("office_d_and_c", "bogus")
    assert result is None


def test_returns_none_when_procedure_classification_blank():
    """Surgery with no procedure_classification → no library lookup."""
    assert fetch_instructions_pdf("", "preop") is None
    assert fetch_instructions_pdf(None, "preop") is None
