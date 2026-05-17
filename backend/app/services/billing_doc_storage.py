"""Disk-backed storage for Insurance Documents.

Files land in BILLING_DOCS_STORAGE_PATH (default: external drive at
/Volumes/OWC External/Insurance Docs). When the path doesn't exist (drive
unmounted), upload raises a 503-style RuntimeError so the user sees a
clear "external drive not mounted" message rather than a corrupt file.

Eventually this module can be swapped for a Google Drive backend without
changing callers — just keep the save/open_stream/delete signatures.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import BinaryIO, Tuple

DEFAULT_PATH = "/Volumes/OWC External/Insurance Docs"


def storage_root() -> Path:
    return Path(os.environ.get("BILLING_DOCS_STORAGE_PATH", DEFAULT_PATH))


def ensure_available() -> Path:
    """Confirm the storage root exists and is a directory. Raises
    RuntimeError otherwise (caller turns it into HTTP 503)."""
    root = storage_root()
    if not root.exists() or not root.is_dir():
        raise RuntimeError(
            f"Storage path is not available: {root} "
            f"(external drive may not be mounted)"
        )
    return root


def save(file_bytes: bytes, original_filename: str) -> Tuple[str, int]:
    """Persist a file on disk. Returns (storage_filename, size_bytes).
    Generates a UUID-prefixed name to avoid collisions."""
    root = ensure_available()
    safe_ext = ""
    if "." in original_filename:
        safe_ext = "." + original_filename.rsplit(".", 1)[-1].lower()[:10]
    storage_name = f"{uuid.uuid4().hex}{safe_ext}"
    out = root / storage_name
    out.write_bytes(file_bytes)
    return storage_name, len(file_bytes)


def open_for_read(storage_filename: str) -> BinaryIO:
    root = ensure_available()
    path = root / storage_filename
    if not path.exists():
        raise FileNotFoundError(str(path))
    return open(path, "rb")


def file_path(storage_filename: str) -> Path:
    return ensure_available() / storage_filename


def delete(storage_filename: str) -> None:
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
