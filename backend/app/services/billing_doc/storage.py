"""Storage for Insurance Documents.

Two backends, selected at runtime by STORAGE_BACKEND env var:

  - "local" (default): files land in BILLING_DOCS_STORAGE_PATH (default
    "/Volumes/OWC External/Insurance Docs"). Used by local-dev + the
    legacy on-Mac deployment.
  - "gcs": files land in gs://<DOCUMENTS_GCS_BUCKET>/billing-docs/<name>.
    Used by Cloud Run prod where local disk is ephemeral.

Public API (save/open_for_read/delete) is identical across backends —
the upload + serve endpoints in routers/billing_documents.py never need
to know which backend is in use.
"""
from __future__ import annotations

import io
import os
import uuid
from pathlib import Path
from typing import BinaryIO, Tuple


DEFAULT_PATH   = "/Volumes/OWC External/Insurance Docs"
GCS_PREFIX     = "billing-docs/"


def _backend() -> str:
    from app.config import settings
    return (settings.storage_backend or "local").lower()


def _gcs_bucket_name() -> str:
    from app.config import settings
    return settings.documents_gcs_bucket


def _gcs_client():
    """Lazy import + cache."""
    global _client
    try:
        return _client
    except NameError:
        from google.cloud import storage  # type: ignore
        _client = storage.Client()
        return _client


# ─── Local disk backend ───────────────────────────────────────────────

def storage_root() -> Path:
    return Path(os.environ.get("BILLING_DOCS_STORAGE_PATH", DEFAULT_PATH))


def ensure_available() -> Path:
    """Local-disk: confirm the storage root exists. Raises RuntimeError
    otherwise (caller turns it into HTTP 503)."""
    root = storage_root()
    if not root.exists() or not root.is_dir():
        raise RuntimeError(
            f"Storage path is not available: {root} "
            f"(external drive may not be mounted)"
        )
    return root


# ─── Shared public API ────────────────────────────────────────────────

def save(file_bytes: bytes, original_filename: str) -> Tuple[str, int]:
    """Persist a file. Returns (storage_filename, size_bytes).
    The storage_filename is a UUID-prefixed name (collision-free)."""
    safe_ext = ""
    if "." in original_filename:
        safe_ext = "." + original_filename.rsplit(".", 1)[-1].lower()[:10]
    storage_name = f"{uuid.uuid4().hex}{safe_ext}"

    if _backend() == "gcs":
        blob = _gcs_client().bucket(_gcs_bucket_name()).blob(GCS_PREFIX + storage_name)
        blob.upload_from_string(file_bytes,
                                  content_type="application/pdf"
                                                  if storage_name.endswith(".pdf")
                                                  else None)
        return storage_name, len(file_bytes)

    root = ensure_available()
    out = root / storage_name
    out.write_bytes(file_bytes)
    return storage_name, len(file_bytes)


def open_for_read(storage_filename: str) -> BinaryIO:
    """Return a file-like object positioned at the start of the doc."""
    if _backend() == "gcs":
        blob = _gcs_client().bucket(_gcs_bucket_name()).blob(GCS_PREFIX + storage_filename)
        if not blob.exists():
            raise FileNotFoundError(f"gs://{_gcs_bucket_name()}/{GCS_PREFIX}{storage_filename}")
        return io.BytesIO(blob.download_as_bytes())

    root = ensure_available()
    path = root / storage_filename
    if not path.exists():
        raise FileNotFoundError(str(path))
    return open(path, "rb")


def file_path(storage_filename: str) -> Path:
    """Local-disk only — returns the Path. Callers that use this won't
    work in GCS mode; use open_for_read() instead. Kept for legacy
    callers that need an actual filesystem path (e.g., FileResponse)."""
    if _backend() == "gcs":
        raise RuntimeError(
            "file_path() not supported in GCS mode — use open_for_read()"
        )
    return ensure_available() / storage_filename


def delete(storage_filename: str) -> None:
    if _backend() == "gcs":
        blob = _gcs_client().bucket(_gcs_bucket_name()).blob(GCS_PREFIX + storage_filename)
        if blob.exists():
            blob.delete()
        return
    path = storage_root() / storage_filename
    if path.exists():
        path.unlink()


def page_count_pdf(file_bytes: bytes) -> int | None:
    """Return PDF page count, or None on failure (non-PDF, corrupted)."""
    try:
        from pypdf import PdfReader
        import io
        return len(PdfReader(io.BytesIO(file_bytes)).pages)
    except Exception:
        return None
