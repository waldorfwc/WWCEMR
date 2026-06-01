"""Patient-facing documents library — procedure-specific instruction PDFs
stored in GCS.

Bucket layout:
    gs://wwc-documents/surgery-instructions/{procedure_classification}/{kind}.pdf

where kind ∈ {"preop", "postop"} and procedure_classification is a value
from Surgery.procedure_classification (e.g. "office_d_and_c", "robotic_tlh").

When a PDF doesn't exist at the expected path, fetch_instructions_pdf
returns None and the calling endpoint surfaces a 404 → patient sees a
"Not available, call us" message in the portal.
"""
from __future__ import annotations

import logging
from typing import Optional

from google.cloud import storage

log = logging.getLogger(__name__)

INSTRUCTIONS_BUCKET = "wwc-documents"
VALID_KINDS = ("preop", "postop")


def fetch_instructions_pdf(
    procedure_classification: Optional[str],
    kind: str,
) -> Optional[bytes]:
    """Return PDF bytes for the requested instructions doc, or None when
    the procedure_classification is blank, the kind isn't recognized, or
    the GCS object doesn't exist. Soft-fails on storage errors — None
    return signals "not available" to the caller."""
    if not procedure_classification or kind not in VALID_KINDS:
        return None
    object_name = (
        f"surgery-instructions/{procedure_classification}/{kind}.pdf"
    )
    try:
        client = storage.Client()
        bucket = client.bucket(INSTRUCTIONS_BUCKET)
        blob = bucket.blob(object_name)
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as e:
        log.warning("instructions PDF fetch failed for %s: %s", object_name, e)
        return None
