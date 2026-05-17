"""Printable PDF for the 'My Job Responsibilities' view.

One row per template the user is responsible for. Columns:
Task · Category · Trained · Not-trained · Date Trained · Assigned by
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


def build_responsibilities_pdf(payload: dict) -> bytes:
    user = payload.get("user", {})
    items = payload.get("items", [])
    summary = payload.get("summary", {})

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter),
                              leftMargin=0.4 * inch, rightMargin=0.4 * inch,
                              topMargin=0.4 * inch, bottomMargin=0.4 * inch,
                              title="My Job Responsibilities")

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"],
                                  textColor=colors.HexColor("#7B2D5E"),
                                  fontSize=16, spaceAfter=4)
    meta_style = ParagraphStyle("meta", parent=styles["Normal"],
                                  fontSize=9, textColor=colors.gray)
    cell_style = ParagraphStyle("cell", parent=styles["Normal"],
                                  fontSize=8, leading=10)

    story = []
    who = user.get("display_name") or user.get("email") or "Unknown"
    story.append(Paragraph(f"My Job Responsibilities — {who}", title_style))
    meta = [f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    if user.get("email"):
        meta.append(user["email"])
    if user.get("practice_role"):
        meta.append(user["practice_role"])
    if summary:
        meta.append(f"{summary.get('total', 0)} total · "
                    f"{summary.get('trained', 0)} trained · "
                    f"{summary.get('untrained', 0)} not-trained")
    story.append(Paragraph(" · ".join(meta), meta_style))
    story.append(Spacer(1, 0.12 * inch))

    headers = ["Task", "Category", "Trained", "Not-trained", "Date Trained", "Assigned by"]
    data = [headers]
    not_trained_rows = []
    for i, it in enumerate(items):
        row_idx = len(data)
        task_para = Paragraph(it.get("question_text") or it.get("title") or "", cell_style)
        trained = bool(it.get("trained"))
        date_trained = it.get("trainee_signed_at") or it.get("trainer_signed_at") or ""
        if date_trained:
            date_trained = date_trained[:10]
        assigned_by = (it.get("trainer_email") or "").split("@")[0] or ""
        data.append([
            task_para,
            (it.get("category") or "").capitalize(),
            "✓" if trained else "",
            "✗" if not trained else "",
            date_trained if trained else "",
            assigned_by if trained else "",
        ])
        if not trained:
            not_trained_rows.append(row_idx)

    col_widths = [3.5 * inch, 1.0 * inch, 0.7 * inch,
                   1.0 * inch, 1.1 * inch, 1.7 * inch]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7B2D5E")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN",      (0, 0), (-1, 0), "CENTER"),
        ("ALIGN",      (1, 1), (-1, -1), "CENTER"),
        ("ALIGN",      (0, 1), (0, -1),  "LEFT"),
        ("FONTSIZE",   (0, 0), (-1, -1), 8),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",       (0, 0), (-1, -1), 0.25, colors.HexColor("#D6C9D2")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
            [colors.white, colors.HexColor("#FAF7F9")]),
        # Trained / Not-trained cell coloring
        ("TEXTCOLOR", (2, 1), (2, -1), colors.HexColor("#2E7D32")),
        ("TEXTCOLOR", (3, 1), (3, -1), colors.HexColor("#C0392B")),
        ("FONTNAME",  (2, 1), (3, -1), "Helvetica-Bold"),
    ])
    # Tint untrained rows so they pop on print
    for idx in not_trained_rows:
        style.add("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#FEE8E5"))
    table.setStyle(style)
    story.append(table)

    if not items:
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph("No assigned responsibilities.",
                                ParagraphStyle("none", parent=styles["Normal"],
                                                  fontSize=10, textColor=colors.gray,
                                                  alignment=1)))

    doc.build(story)
    return buf.getvalue()
