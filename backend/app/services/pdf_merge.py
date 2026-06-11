"""Concat PDFs into a single temp file. Caller owns deletion (use a finally)."""
import io
import os
import tempfile
from pypdf import PdfWriter, PdfReader


def _read_input(path_or_key: str) -> bytes:
    """Resolve a stored document to bytes.

    Inputs can be either:
      - a local file path (legacy / dev workstation) — used as-is
      - a storage key (Cloud Run on GCS) — fetched via the storage layer
    """
    if os.path.isfile(path_or_key):
        with open(path_or_key, "rb") as f:
            return f.read()
    # Defer import so tests / scripts that don't need GCS don't pay the
    # google-cloud-storage import cost.
    from app.services.storage import read_blob
    try:
        return read_blob(path_or_key)
    except FileNotFoundError:
        raise FileNotFoundError(path_or_key)


def merge_pdfs(paths: list[str]) -> str:
    """Merge the PDFs at `paths` into a single temp PDF. Returns the temp path.

    `paths` accepts both local filesystem paths and storage keys; the helper
    falls back to the storage backend (GCS on Cloud Run) when a local file
    isn't found.

    Raises FileNotFoundError if any input can't be resolved.
    Raises ValueError if an input isn't a readable PDF.
    Caller is responsible for os.unlink(path) when done.
    """
    writer = PdfWriter()
    for p in paths:
        data = _read_input(p)
        try:
            reader = PdfReader(io.BytesIO(data))
        except Exception as e:
            raise ValueError(f"Failed to read PDF {p}: {e}")
        for page in reader.pages:
            writer.add_page(page)

    fd, out_path = tempfile.mkstemp(suffix=".pdf", prefix="fax-merged-")
    try:
        with os.fdopen(fd, "wb") as f:
            writer.write(f)
    except Exception:
        os.unlink(out_path)
        raise
    return out_path
