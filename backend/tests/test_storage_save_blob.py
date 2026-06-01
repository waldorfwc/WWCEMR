"""storage.save_blob + is_legacy_local_path."""
from unittest.mock import patch, MagicMock


def _reload_storage(monkeypatch, backend="gcs"):
    """Helper: set env var then reload the module so module-level _STORAGE_BACKEND
    picks it up."""
    monkeypatch.setenv("STORAGE_BACKEND", backend)
    import importlib
    from app.services import storage as s
    importlib.reload(s)
    return s


def test_save_blob_gcs_returns_prefixed_key_with_ext(monkeypatch):
    s = _reload_storage(monkeypatch, "gcs")
    fake_blob = MagicMock()
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    with patch.object(s, "_gcs_client", return_value=fake_client):
        key = s.save_blob(prefix="pellet-attachments",
                              body=b"%PDF-1.4 test",
                              filename="invoice.pdf")
    assert key.startswith("pellet-attachments/")
    assert key.endswith(".pdf")
    fake_blob.upload_from_string.assert_called_once()
    # Verify content-type was inferred from filename
    _, kwargs = fake_blob.upload_from_string.call_args
    assert kwargs.get("content_type") == "application/pdf"


def test_save_blob_gcs_no_ext_when_filename_has_none(monkeypatch):
    s = _reload_storage(monkeypatch, "gcs")
    fake_blob = MagicMock()
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    with patch.object(s, "_gcs_client", return_value=fake_client):
        key = s.save_blob(prefix="x", body=b"raw", filename="readme")
    # No extension to preserve
    assert key.startswith("x/")
    assert "." not in key.split("/")[-1]


def test_save_blob_local_writes_to_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCUMENTS_LOCAL_ROOT", str(tmp_path))
    s = _reload_storage(monkeypatch, "local")
    key = s.save_blob(prefix="x", body=b"hello", filename="a.txt")
    assert key.startswith("x/")
    assert key.endswith(".txt")
    assert (tmp_path / key).read_bytes() == b"hello"


def test_is_legacy_local_path():
    # Need a fresh reload so we get current module state
    import importlib
    from app.services import storage as s
    importlib.reload(s)
    assert s.is_legacy_local_path("/Users/wwcclaudecode/foo.pdf")
    assert s.is_legacy_local_path("/Volumes/OWC External/x.pdf")
    assert s.is_legacy_local_path("./uploads/active_ar_docs/abc.pdf")
    assert s.is_legacy_local_path("../uploads/x.pdf")
    assert not s.is_legacy_local_path("pellet-attachments/abc.pdf")
    assert not s.is_legacy_local_path("surgery-files/uuid.pdf")
    assert not s.is_legacy_local_path("")
    assert not s.is_legacy_local_path(None)


def test_content_disposition_ascii_uses_plain_filename():
    from app.services.storage import _content_disposition
    out = _content_disposition("attachment", "hello.pdf")
    assert out == 'attachment; filename="hello.pdf"'
    # Must be encodable as latin-1 (HTTP header constraint)
    out.encode("latin-1")


def test_content_disposition_non_ascii_uses_rfc5987(monkeypatch):
    """Filenames containing non-ASCII (e.g. \\u202f) must not crash header
    encoding. Browsers see the original via filename*; older clients get
    the ASCII fallback."""
    from app.services.storage import _content_disposition
    # U+202F (NARROW NO-BREAK SPACE) appears in real EOB filenames
    out = _content_disposition("attachment", "KENNEDY, MELISSA EOB.pdf")
    assert "filename=" in out
    assert "filename*=UTF-8''" in out
    assert "%E2%80%AF" in out   # the encoded U+202F
    # Crucially: the whole header is latin-1 safe
    out.encode("latin-1")


def test_content_disposition_empty_filename_uses_download():
    from app.services.storage import _content_disposition
    assert 'filename="download"' in _content_disposition("inline", "")
    assert 'filename="download"' in _content_disposition("inline", None or "")
