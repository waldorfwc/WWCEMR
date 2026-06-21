"""LARC inventory PDF export.

Renders the on-hand device list (one row per device) as a landscape PDF,
mirroring the pellet inventory PDF's reportlab approach.
"""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
)

COLUMNS = ["Our ID", "Device Type", "Lot", "Expiration",
           "Location", "Ownership", "Status", "Assignee"]


def build_pdf(rows: list[dict], *, generated_by: str = "system") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter),
                            leftMargin=0.4 * inch, rightMargin=0.4 * inch,
                            topMargin=0.4 * inch, bottomMargin=0.4 * inch,
                            title="LARC Inventory — On Hand")

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"],
                                 textColor=colors.HexColor("#7B2D5E"),
                                 fontSize=16, spaceAfter=4)
    meta_style = ParagraphStyle("meta", parent=styles["Normal"],
                                fontSize=8, textColor=colors.gray)

    story = []
    story.append(Paragraph("LARC Inventory — On Hand", title_style))

    meta_bits = [datetime.now().strftime("Generated %Y-%m-%d %H:%M")]
    if generated_by and generated_by != "system":
        meta_bits.append(f"by {generated_by}")
    story.append(Paragraph(" · ".join(meta_bits), meta_style))
    story.append(Spacer(1, 0.1 * inch))

    data = [COLUMNS]
    for r in rows:
        data.append([str(r.get(c, "") or "") for c in COLUMNS])

    col_widths = [1.0 * inch, 1.6 * inch, 1.2 * inch, 1.0 * inch,
                  1.3 * inch, 1.0 * inch, 1.2 * inch, 1.7 * inch]
    table = Table(data, colWidths=col_widths, repeatRows=1)

    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7B2D5E")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN",      (0, 0), (-1, 0), "CENTER"),
        ("FONTSIZE",   (0, 0), (-1, -1), 8),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",       (0, 0), (-1, -1), 0.25, colors.HexColor("#D6C9D2")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
            [colors.white, colors.HexColor("#FAF7F9")]),
    ])
    table.setStyle(style)
    story.append(table)

    doc.build(story)
    return buf.getvalue()
