"""Shared PDF building blocks for the pellet module.

Centralizes brand colors, paragraph styles, header helpers, and the meta-box
table style so future transfer / order / disposal PDFs render with the same
look as the daily-count PDF without copy-paste drift.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle


# ─── Brand palette ────────────────────────────────────────────────
PLUM       = HexColor("#7B2D5E")
PLUM_LIGHT = HexColor("#F5E8EE")
INK        = HexColor("#2A1B23")
MUTED      = HexColor("#6B5560")
RULE       = HexColor("#E5D7DE")
GREEN      = HexColor("#0F8A4D")
AMBER      = HexColor("#B45309")
RED        = HexColor("#B91C1C")
WHITE      = HexColor("#FFFFFF")


LOC_LABEL = {
    "white_plains": "White Plains",
    "brandywine":   "Brandywine",
    "arlington":    "Arlington",
    "all":          "All locations",
}


def fmt_ts(dt: Optional[datetime]) -> str:
    """Format a datetime as 'Mar 12, 2026 · 2:35 PM' or '—' when None."""
    if not dt:
        return "—"
    return dt.strftime("%b %d, %Y · %I:%M %p")


def build_styles() -> dict:
    """Return the shared paragraph styles. Each caller picks what it needs."""
    base = getSampleStyleSheet()["Normal"]
    return {
        "h":       ParagraphStyle("h", parent=base, fontName="Helvetica-Bold",
                                    fontSize=18, textColor=PLUM, spaceAfter=2),
        "sub":     ParagraphStyle("sub", parent=base, fontName="Helvetica-Oblique",
                                    fontSize=10, textColor=PLUM, spaceAfter=10),
        "section": ParagraphStyle("section", parent=base, fontName="Helvetica-Bold",
                                    fontSize=11, textColor=PLUM,
                                    spaceBefore=8, spaceAfter=4),
        "body":    ParagraphStyle("body", parent=base, fontName="Helvetica",
                                    fontSize=9, textColor=INK, leading=12),
        "muted":   ParagraphStyle("muted", parent=base, fontName="Helvetica",
                                    fontSize=8, textColor=MUTED, leading=11),
    }


def header_block(subtitle: str, styles: dict) -> list:
    """The brand header used at the top of every pellet PDF."""
    return [
        Paragraph("Waldorf Women's Care &amp; Aesthetics", styles["h"]),
        Paragraph(subtitle, styles["sub"]),
    ]


def meta_table(rows: list[list[str]], left_col_in: float = 1.5,
                 right_col_in: float = 5.85) -> Table:
    """Two-column key/value table used in the document header section.

    `rows` is a list of [label, value] pairs.
    """
    from reportlab.lib.units import inch
    t = Table(rows, colWidths=[left_col_in * inch, right_col_in * inch])
    t.setStyle(TableStyle([
        ("FONTNAME",    (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 9),
        ("TEXTCOLOR",   (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR",   (1, 0), (1, -1), INK),
        ("BACKGROUND",  (0, 0), (-1, -1), PLUM_LIGHT),
        ("BOX",         (0, 0), (-1, -1), 0.5, RULE),
        ("INNERGRID",   (0, 0), (-1, -1), 0.3, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",(0, 0), (-1, -1), 6),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
    ]))
    return t


def footer_line(extra: str, styles: dict) -> Paragraph:
    """Bottom-of-document generation timestamp + any extra identifier."""
    when = datetime.utcnow().strftime("%B %d, %Y · %I:%M %p UTC")
    return Paragraph(f"Generated {when} · {extra}", styles["muted"])
