"""HTTP response helpers."""
import urllib.parse


def content_disposition(filename: str, disposition: str = "inline") -> str:
    """Build a latin-1-safe Content-Disposition header value.

    HTTP header values are latin-1 encoded. A filename containing a non-latin-1
    character — e.g. the narrow no-break space U+202F that some scanners/OSes
    inject, or accented patient names — would raise UnicodeEncodeError when the
    response is sent, surfacing as an opaque 500. We emit an ASCII fallback
    ``filename="…"`` (non-ASCII stripped) plus an RFC 5987
    ``filename*=UTF-8''…`` that carries the real, fully-Unicode name. Modern
    browsers honour filename*; old ones fall back to the ASCII form.
    """
    name = (filename or "document").replace('"', '').replace("\n", "").replace("\r", "")
    ascii_name = name.encode("ascii", "ignore").decode().strip() or "document"
    utf8_name = urllib.parse.quote(name, safe="")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
