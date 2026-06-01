"""Render printable QR-code PNGs for reputation profiles."""
import io
import os

import qrcode

# Public reviews subdomain. Override via env in case we change domains.
REVIEWS_BASE_URL = os.environ.get(
    "REVIEWS_BASE_URL", "https://reviews.waldorfwomenscare.com")


def render_profile_qr_png(qr_token: str) -> bytes:
    """Generate a PNG that encodes the public review URL for `qr_token`.
    Printable size: ~600px, with a quiet zone border."""
    url = f"{REVIEWS_BASE_URL}/r/{qr_token}"
    img = qrcode.make(url, box_size=12, border=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
