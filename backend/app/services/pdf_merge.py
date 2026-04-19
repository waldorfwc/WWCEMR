"""Concat PDFs into a single temp file. Caller owns deletion (use a finally)."""
import os
import tempfile
from pypdf import PdfWriter, PdfReader


def merge_pdfs(paths: list[str]) -> str:
    """Merge the PDFs at `paths` into a single temp PDF. Returns the temp path.

    Raises FileNotFoundError if any input is missing.
    Raises ValueError if an input isn't a readable PDF.
    Caller is responsible for os.unlink(path) when done.
    """
    for p in paths:
        if not os.path.isfile(p):
            raise FileNotFoundError(p)

    writer = PdfWriter()
    for p in paths:
        try:
            reader = PdfReader(p)
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
