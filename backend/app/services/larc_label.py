"""Generate a printable label PDF for a LARC device. The label is sized
for a standard 2.25" x 1.25" thermal address label (Dymo 30334 / similar)
and includes:

  - WWC logo strip
  - Device our_id (large)
  - Device type + manufacturer
  - Lot # + expiration
  - QR code that opens /larc/devices/{id} in the app for fast lookup

Output: bytes of a single-page PDF.
"""
from __future__ import annotations

import io
import os
from typing import Optional

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import inch
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app.models.larc import LarcDevice


# Label dimensions — 2.25" wide x 1.25" tall (Dymo 30334)
LABEL_W = 2.25 * inch
LABEL_H = 1.25 * inch
PLUM = colors.HexColor("#7B2D5E")


def _qr_image(payload: str) -> ImageReader:
    qr = qrcode.QRCode(version=None, box_size=4, border=1,
                        error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def render_device_label(d: LarcDevice, *, base_url: Optional[str] = None) -> bytes:
    """Render a single-label PDF for the given device. `base_url` controls
    what the QR code points to; pass the app's public URL."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(LABEL_W, LABEL_H))

    # Header strip (plum)
    c.setFillColor(PLUM)
    c.rect(0, LABEL_H - 0.18 * inch, LABEL_W, 0.18 * inch, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(0.08 * inch, LABEL_H - 0.13 * inch, "WALDORF WOMEN'S CARE — LARC")

    # Device our_id (large)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(0.1 * inch, LABEL_H - 0.42 * inch, d.our_id or "")

    # Device type + manufacturer
    type_name = d.device_type.name if d.device_type else "Unknown"
    mfr = d.device_type.manufacturer if d.device_type else ""
    c.setFont("Helvetica-Bold", 8)
    c.drawString(0.1 * inch, LABEL_H - 0.58 * inch, type_name)
    if mfr:
        c.setFont("Helvetica", 6.5)
        c.setFillColor(colors.HexColor("#666666"))
        c.drawString(0.1 * inch, LABEL_H - 0.7 * inch, mfr)

    # Lot + expiration
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 6.5)
    if d.manufacturer_lot:
        c.drawString(0.1 * inch, LABEL_H - 0.85 * inch, f"LOT {d.manufacturer_lot}")
    if d.expiration_date:
        c.drawString(0.1 * inch, LABEL_H - 0.97 * inch, f"EXP {d.expiration_date}")

    # Location
    if d.location:
        c.setFillColor(colors.HexColor("#666666"))
        c.drawString(0.1 * inch, LABEL_H - 1.1 * inch, d.location.replace("_", " ").upper())

    # QR code (right side, ~0.85" square)
    qr_payload = f"{base_url or 'http://localhost:5173'}/larc/devices/{d.id}"
    qr_img = _qr_image(qr_payload)
    qr_size = 0.85 * inch
    qr_x = LABEL_W - qr_size - 0.08 * inch
    qr_y = (LABEL_H - qr_size) / 2 - 0.05 * inch
    c.drawImage(qr_img, qr_x, qr_y, width=qr_size, height=qr_size,
                preserveAspectRatio=True, mask="auto")

    c.showPage()
    c.save()
    return buf.getvalue()
