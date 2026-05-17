"""WWC-branded PDF rendering for appeal letters."""
from __future__ import annotations

import io
import os
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak,
)


PLUM = colors.HexColor("#7B2D5E")
PLUM_LIGHT = colors.HexColor("#F4E8EE")
GRAY = colors.HexColor("#666666")
GRAY_LIGHT = colors.HexColor("#DDDDDD")

LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "wwc-logo.png")


def _styles():
    base = getSampleStyleSheet()
    out = {}
    out["body"] = ParagraphStyle(
        "body", parent=base["BodyText"],
        fontName="Helvetica", fontSize=10.5, leading=14,
        spaceBefore=2, spaceAfter=4,
    )
    out["body_bold"] = ParagraphStyle(
        "body_bold", parent=out["body"], fontName="Helvetica-Bold",
    )
    out["small"] = ParagraphStyle(
        "small", parent=base["BodyText"], fontName="Helvetica", fontSize=8.5,
        leading=11, textColor=GRAY,
    )
    out["heading"] = ParagraphStyle(
        "heading", parent=base["Heading2"], fontName="Helvetica-Bold",
        fontSize=11, leading=14, textColor=PLUM, spaceBefore=8, spaceAfter=4,
    )
    return out


def render_pdf(subject: str, body: str, output_path: Optional[str] = None) -> bytes:
    """Render an appeal letter PDF. If output_path is provided, also writes to disk.
    Returns the PDF bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=0.6 * inch, bottomMargin=0.65 * inch,
        title=subject[:80] if subject else "Appeal Letter",
    )
    styles = _styles()

    story = []

    # Letterhead row: logo + practice name (right-aligned)
    if os.path.exists(LOGO_PATH):
        try:
            logo = Image(LOGO_PATH, width=0.7 * inch, height=0.7 * inch)
        except Exception:
            logo = ""
    else:
        logo = ""
    header_table = Table(
        [[logo, Paragraph(
            '<font color="#7B2D5E" size="14"><b>WWC Gynecology &amp; Aesthetics</b></font><br/>'
            '<font color="#666666" size="9">Appeals Correspondence</font>',
            styles["small"],
        )]],
        colWidths=[0.9 * inch, 5.5 * inch],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.75, PLUM),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.15 * inch))

    # Subject (small, gray, above body)
    if subject:
        story.append(Paragraph(
            f'<font color="#666666" size="8.5">SUBJECT: {_xml_escape(subject)}</font>',
            styles["small"],
        ))
        story.append(Spacer(1, 0.08 * inch))

    # Body — render line-by-line, preserve blank lines as paragraph breaks
    paragraphs = _split_paragraphs(body or "")
    for para in paragraphs:
        text = _xml_escape(para).replace("\n", "<br/>")
        story.append(Paragraph(text, styles["body"]))

    pdf_bytes = b""
    try:
        doc.build(story)
        pdf_bytes = buf.getvalue()
    finally:
        buf.close()

    if output_path and pdf_bytes:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)

    return pdf_bytes


def _split_paragraphs(text: str) -> list:
    out = []
    buf = []
    for line in text.split("\n"):
        if line.strip() == "":
            if buf:
                out.append("\n".join(buf))
                buf = []
        else:
            buf.append(line)
    if buf:
        out.append("\n".join(buf))
    return out


def _xml_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
