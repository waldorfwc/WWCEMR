"""Patient-uploaded document storage in GCS, plus the static-PDF read
path used by the clearance template endpoint.

Bucket: configured via DOCUMENTS_GCS_BUCKET env var (same lookup as
app.services.storage); default `wwc-app-docs`. Patient uploads live
under: gs://{bucket}/surgery-uploads/{surgery_id}/{kind}/{ts}_{safe}.

Validation:
  - Max 10 MB per upload
  - Content-Type must be one of ALLOWED_MIME
  - Magic-byte sniff verifies the bytes match the declared Content-Type
    so a renamed .exe can't slip through as an .pdf
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from app.utils.dt import now_utc_naive
from typing import Optional

from google.cloud import storage
from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgeryDocument

log = logging.getLogger(__name__)

BUCKET = os.environ.get("DOCUMENTS_GCS_BUCKET", "wwc-app-docs")
ALLOWED_MIME = (
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
)
MAX_BYTES = 10 * 1024 * 1024

# Magic-byte signatures for each MIME we accept. First N bytes must match.
_MAGIC = {
    "application/pdf": (b"%PDF",),
    "image/jpeg":      (b"\xff\xd8\xff",),
    "image/png":       (b"\x89PNG\r\n\x1a\n",),
    "image/heic":      (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypheim"),
    # HEIC magic appears at offset 4-12 (after the box-size prefix), so the
    # check is "any of these substrings appears in the first 32 bytes."
}


class UploadError(Exception):
    """Raised when an upload can't be stored. Message is patient-facing."""
    def __init__(self, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _safe_filename(name: str) -> str:
    """Strip path components + non-alphanumerics so the GCS object name is
    predictable and free of injection risk."""
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem, ext = ext, ""
    stem = re.sub(r"[^a-zA-Z0-9_.-]", "_", stem)[:80] or "upload"
    ext  = re.sub(r"[^a-zA-Z0-9]", "", ext)[:8] or "bin"
    return f"{stem}.{ext}"


def _magic_matches(content_type: str, first_bytes: bytes) -> bool:
    sigs = _MAGIC.get(content_type)
    if not sigs:
        return False
    if content_type == "image/heic":
        return any(s in first_bytes[:32] for s in sigs)
    return any(first_bytes.startswith(s) for s in sigs)


def store_upload(db: Session, surgery: Surgery, *, kind: str,
                   filename: str, file_bytes: bytes,
                   content_type: str,
                   uploaded_by: str) -> SurgeryDocument:
    """Validate → write to GCS → create SurgeryDocument row."""
    if len(file_bytes) > MAX_BYTES:
        raise UploadError(
            f"That file is too large. The maximum is "
            f"{MAX_BYTES // (1024 * 1024)} MB.",
            status_code=413,
        )
    if content_type not in ALLOWED_MIME:
        raise UploadError(
            "That file type isn't supported. Please upload a PDF, JPEG, "
            "PNG, or HEIC.",
            status_code=415,
        )
    if not _magic_matches(content_type, file_bytes[:64]):
        raise UploadError(
            "The file content doesn't match its type. Please re-export "
            "and try again.",
            status_code=415,
        )

    ts = now_utc_naive().strftime("%Y%m%d-%H%M%S")
    safe = _safe_filename(filename)
    object_path = f"surgery-uploads/{surgery.id}/{kind}/{ts}_{safe}"

    client = storage.Client()
    bucket = client.bucket(BUCKET)
    blob   = bucket.blob(object_path)
    blob.upload_from_string(file_bytes, content_type=content_type)

    doc = SurgeryDocument(
        surgery_id=surgery.id,
        kind=kind,
        filename=filename,
        gcs_path=object_path,
        content_type=content_type,
        size_bytes=len(file_bytes),
        uploaded_by=uploaded_by,
    )
    db.add(doc); db.commit(); db.refresh(doc)
    return doc


def signed_download_url(doc: SurgeryDocument,
                          ttl_minutes: int = 5) -> str:
    """V4 signed URL good for the requested TTL. Default is 5 min.

    On Cloud Run, the default credentials come from the metadata server
    and have no private key — they can't sign directly. We delegate
    signing to the IAM signBlob API via the service account itself
    (requires roles/iam.serviceAccountTokenCreator on the SA → SA).
    """
    import google.auth
    from google.auth.transport import requests as auth_requests

    credentials, _ = google.auth.default()
    auth_request = auth_requests.Request()
    credentials.refresh(auth_request)
    sa_email = getattr(credentials, "service_account_email", None)

    client = storage.Client(credentials=credentials)
    bucket = client.bucket(BUCKET)
    blob   = bucket.blob(doc.gcs_path)
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(minutes=ttl_minutes),
        method="GET",
        service_account_email=sa_email,
        access_token=credentials.token,
    )


def stream_static_pdf(gcs_path: str) -> Optional[bytes]:
    """Read a static object (e.g. clearance/template.pdf) — returns the
    raw bytes or None if the object doesn't exist. Soft-fail on storage
    errors so the calling endpoint can render a friendly 404."""
    try:
        client = storage.Client()
        bucket = client.bucket(BUCKET)
        blob   = bucket.blob(gcs_path)
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as e:
        log.warning("static PDF fetch failed for %s: %s", gcs_path, e)
        return None
