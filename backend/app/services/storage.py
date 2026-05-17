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
import os
from typing import Optional
from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse

_STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").lower()
_GCS_BUCKET = os.environ.get("DOCUMENTS_GCS_BUCKET", "wwc-app-docs")
_CHUNK_SIZE = 64 * 1024  # 64 KB streaming chunks


def _gcs_client():
    """Lazy import + cache the GCS client. Avoids forcing the dependency
    on local-dev installs that don't have google-cloud-storage."""
    global _client
    try:
        return _client
    except NameError:
        from google.cloud import storage  # type: ignore
        _client = storage.Client()
        return _client


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
            headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
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
