"""Document storage abstraction.

Resolves a document's storage location and returns a FastAPI response
for it. Two backends are supported, selected via STORAGE_BACKEND env var:

  - "local" (default): serve from a local filesystem path under
    settings.documents_dir / settings.intake_dir / etc.
  - "gcs":  serve from a GCS bucket (settings.documents_gcs_bucket),
    streamed back through the backend so we authenticate + audit-log
    each access rather than handing out signed URLs.

In production on Cloud Run, the backend's service account has
storage.objectAdmin on the bucket, so credentials come from ADC.
"""
import functools
import mimetypes
import os
import unicodedata
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional
from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse


def _content_disposition(disposition: str, filename: str) -> str:
    """Build a Content-Disposition header value that is safe to encode as
    latin-1. Non-ASCII filenames get an RFC 5987 `filename*` parameter so
    modern browsers see the original name; the ASCII fallback strips
    diacritics and replaces any remaining non-latin-1 byte with `_`."""
    ascii_fallback = (
        unicodedata.normalize("NFKD", filename or "")
        .encode("ascii", "ignore").decode("ascii")
    ) or "download"
    needs_utf8 = any(ord(c) > 127 for c in (filename or ""))
    if not needs_utf8:
        return f'{disposition}; filename="{ascii_fallback}"'
    encoded = urllib.parse.quote(filename, safe="")
    return (f'{disposition}; filename="{ascii_fallback}"; '
              f"filename*=UTF-8''{encoded}")

from app.config import settings

_STORAGE_BACKEND = settings.storage_backend.lower()
_GCS_BUCKET = settings.documents_gcs_bucket
_CHUNK_SIZE = 64 * 1024  # 64 KB streaming chunks


@functools.lru_cache(maxsize=1)
def _gcs_client():
    """Lazy import + cache the GCS client. Avoids forcing the dependency
    on local-dev installs that don't have google-cloud-storage."""
    from google.cloud import storage  # type: ignore
    return storage.Client()


def serve_blob(
    *,
    local_path: Optional[str],
    gcs_object: Optional[str],
    media_type: str,
    filename: str,
    disposition: str = "attachment",
):
    """Return a FastAPI response that serves a stored document.

    Pass both `local_path` and `gcs_object` — the backend will pick based
    on STORAGE_BACKEND. Raises HTTPException(404) if neither resolves.

      local_path:  absolute path on disk (used when STORAGE_BACKEND=local)
      gcs_object:  object key within the docs bucket (used when =gcs).
                   Should NOT start with a leading slash.
    """
    if _STORAGE_BACKEND == "gcs":
        if not gcs_object:
            raise HTTPException(status_code=404, detail="No GCS object key")
        client = _gcs_client()
        blob = client.bucket(_GCS_BUCKET).blob(gcs_object)
        if not blob.exists():
            raise HTTPException(status_code=404, detail="Document not found in storage")

        def iter_blob():
            with blob.open("rb") as f:
                while True:
                    chunk = f.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(
            iter_blob(),
            media_type=media_type,
            headers={"Content-Disposition":
                       _content_disposition(disposition, filename)},
        )

    # default: local filesystem
    if not local_path or not os.path.isfile(local_path):
        raise HTTPException(status_code=404, detail="File not locally available")
    return FileResponse(
        path=local_path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


def gcs_object_for_patient_document(relative_path: str) -> str:
    """Map a patient_documents relative path (e.g.
    'Document/10010/HIPAA Forms-...pdf') to its GCS key under the
    `extracted/` prefix in the docs bucket."""
    rp = relative_path.lstrip("/")
    return f"extracted/{rp}"


def gcs_object_for_intake(relative_path: str) -> str:
    """Map an intake doc relative path to its GCS key under `intake/`."""
    rp = relative_path.lstrip("/")
    return f"intake/{rp}"


def using_gcs() -> bool:
    return _STORAGE_BACKEND == "gcs"


def save_blob(*, prefix: str, body: bytes,
              filename: str = "") -> str:
    """Persist bytes to storage and return the storage key.
    Caller stores the returned key in DB. Format: `{prefix}/{uuid}{.ext}`.

    On GCS backend writes to `gs://<_GCS_BUCKET>/{key}`.
    On local backend writes to `{DOCUMENTS_LOCAL_ROOT}/{key}` (default
    /var/data/wwc-docs).
    """
    safe_ext = ""
    if filename and "." in filename:
        safe_ext = "." + filename.rsplit(".", 1)[-1].lower()[:10]
    key = f"{prefix}/{uuid.uuid4().hex}{safe_ext}"

    if _STORAGE_BACKEND == "gcs":
        client = _gcs_client()
        content_type = (mimetypes.guess_type(filename)[0]
                        if filename else None) or "application/octet-stream"
        blob = client.bucket(_GCS_BUCKET).blob(key)
        blob.upload_from_string(body, content_type=content_type)
        return key

    root = Path(settings.documents_local_root)
    out = root / key
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    return key


def save_blob_with_key(*, key: str, body: bytes,
                          content_type: Optional[str] = None) -> str:
    """Like save_blob, but the caller picks the key. Used when the key is
    meaningful (e.g. preview-cache lookups keyed by a stable id rather than
    a random uuid)."""
    if _STORAGE_BACKEND == "gcs":
        client = _gcs_client()
        blob = client.bucket(_GCS_BUCKET).blob(key)
        blob.upload_from_string(body,
                                  content_type=content_type or "application/octet-stream")
        return key

    root = Path(settings.documents_local_root)
    out = root / key
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    return key


def read_blob(key: str) -> bytes:
    """Download bytes for a stored key. Raises FileNotFoundError if the
    object/file doesn't exist."""
    if _STORAGE_BACKEND == "gcs":
        client = _gcs_client()
        blob = client.bucket(_GCS_BUCKET).blob(key)
        if not blob.exists():
            raise FileNotFoundError(f"gs://{_GCS_BUCKET}/{key}")
        return blob.download_as_bytes()

    root = Path(settings.documents_local_root)
    p = root / key
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return p.read_bytes()


def is_legacy_local_path(path: Optional[str]) -> bool:
    """True if `path` looks like a pre-migration absolute filesystem path
    rather than a GCS object key. GCS keys never start with `/`."""
    if not path:
        return False
    return (path.startswith("/")     # absolute (Mac Mini, mounted volume)
              or path.startswith("./")   # relative — `./uploads/...`
              or path.startswith("../"))


def delete_blob(key: str) -> bool:
    """Best-effort delete. Returns True if the blob was removed, False if
    it didn't exist. Used by TTL sweeps (e.g. bank-recon preview CSV
    cleanup). Does NOT raise on missing — callers can re-call safely.
    """
    if not key:
        return False
    if _STORAGE_BACKEND == "gcs":
        client = _gcs_client()
        blob = client.bucket(_GCS_BUCKET).blob(key)
        try:
            blob.delete()
            return True
        except Exception:
            return False
    root = Path(settings.documents_local_root)
    p = root / key
    if p.is_file():
        try:
            p.unlink()
            return True
        except Exception:
            return False
    return False


def list_blob_keys(prefix: str) -> list[str]:
    """Enumerate stored keys under a prefix. Used by sweeps."""
    if _STORAGE_BACKEND == "gcs":
        client = _gcs_client()
        return [
            b.name for b in client.bucket(_GCS_BUCKET).list_blobs(prefix=prefix)
        ]
    root = Path(settings.documents_local_root)
    out: list[str] = []
    p = root / prefix
    if p.is_dir():
        for f in p.rglob("*"):
            if f.is_file():
                out.append(str(f.relative_to(root)))
    return out


def blob_metadata(key: str) -> Optional[dict]:
    """Return creation time + size for a key, or None if missing."""
    if _STORAGE_BACKEND == "gcs":
        client = _gcs_client()
        blob = client.bucket(_GCS_BUCKET).get_blob(key)
        if not blob:
            return None
        return {"created": blob.time_created, "size": blob.size}
    root = Path(settings.documents_local_root)
    p = root / key
    if not p.is_file():
        return None
    stat = p.stat()
    from datetime import datetime as _dt, timezone
    return {"created": _dt.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            "size": stat.st_size}
