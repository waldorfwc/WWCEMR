"""Generates a justification letter for CPT modifier 22 (increased
procedural services) and saves it as a SurgeryFile.

The AI billing service calls this whenever it returns one or more CPTs
with modifier 22 — the letter is what billing attaches to the claim
submission to justify the higher reimbursement request.
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle,
)
from sqlalchemy.orm import Session

from app.config import settings
from app.models.surgery import Surgery, SurgeryFile

PLUM = colors.HexColor("#7B2D5E")
GRAY = colors.HexColor("#666666")
LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "wwc-logo.png")


def _styles():
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle("body", parent=base["BodyText"],
                                fontName="Helvetica", fontSize=10.5, leading=14,
                                spaceBefore=2, spaceAfter=4),
        "bold": ParagraphStyle("bold", parent=base["BodyText"],
                                fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                                spaceBefore=2, spaceAfter=4),
        "heading": ParagraphStyle("heading", parent=base["Heading2"],
                                  fontName="Helvetica-Bold", fontSize=12,
                                  leading=15, textColor=PLUM,
                                  spaceBefore=8, spaceAfter=4),
        "small": ParagraphStyle("small", parent=base["BodyText"],
                                fontName="Helvetica", fontSize=8.5,
                                leading=11, textColor=GRAY),
    }


def _render_pdf(s: Surgery, mod22_cpts: list[dict]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
    st = _styles()
    story = []

    # Header — logo + practice info
    if os.path.exists(LOGO_PATH):
        try:
            story.append(Image(LOGO_PATH, width=2.0 * inch, height=0.65 * inch))
        except Exception:
            pass
    story.append(Spacer(1, 6))
    story.append(Paragraph(settings.practice_name or "Waldorf Women's Care", st["bold"]))
    if settings.practice_address:
        story.append(Paragraph(settings.practice_address, st["small"]))
    if settings.practice_phone:
        story.append(Paragraph(settings.practice_phone, st["small"]))
    if settings.practice_npi:
        story.append(Paragraph(f"NPI {settings.practice_npi}", st["small"]))
    story.append(Spacer(1, 12))

    # Date + addressee
    story.append(Paragraph(datetime.utcnow().strftime("%B %d, %Y"), st["body"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(s.primary_insurance or "Primary Insurance Carrier", st["bold"]))
    story.append(Paragraph("Claims Review Department", st["body"]))
    story.append(Spacer(1, 10))

    # Subject
    story.append(Paragraph(
        f"<b>Re:</b> Justification for CPT Modifier 22 — "
        f"{s.patient_name or 'Patient'} (DOB {s.dob or '—'}, "
        f"Member {s.primary_member_id or '—'})",
        st["body"]
    ))
    if s.modmed_claim_number:
        story.append(Paragraph(f"<b>Claim #:</b> {s.modmed_claim_number}", st["body"]))
    if s.scheduled_date:
        story.append(Paragraph(f"<b>Date of service:</b> {s.scheduled_date}", st["body"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("To Whom It May Concern:", st["body"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph(
        "We are submitting this claim with CPT modifier 22 (increased procedural "
        "services) and respectfully request additional reimbursement to reflect "
        "the substantially greater work, time, and complexity required to "
        "perform the procedure(s) listed below.",
        st["body"]
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Procedure(s) with Modifier 22:", st["heading"]))

    rows = [["CPT", "Modifier", "POS", "Description", "Justification"]]
    for c in mod22_cpts:
        rows.append([
            c.get("code", "—"),
            c.get("modifier", "22"),
            c.get("pos", "—"),
            Paragraph(c.get("description", "—"), st["body"]),
            Paragraph(c.get("rationale_22") or
                       "Significantly greater operative time/complexity than typical.",
                       st["body"]),
        ])
    t = Table(rows, colWidths=[0.55 * inch, 0.65 * inch, 0.45 * inch, 1.6 * inch, 3.6 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PLUM),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    # Closing
    story.append(Paragraph(
        "The operative report (enclosed) details the specific findings that "
        "drove the increased work effort. Based on the documentation, we "
        "request reimbursement at 120–150% of the standard CPT allowable, "
        "consistent with AMA guidance for substantial modifier-22 cases.",
        st["body"]
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Please contact our billing office at "
        f"{settings.practice_phone or '(office phone)'} with any questions.",
        st["body"]
    ))
    story.append(Spacer(1, 12))

    # Signature block — re-uses the appeal signer config
    story.append(Paragraph("Sincerely,", st["body"]))
    story.append(Spacer(1, 26))
    signer_name = (
        # PracticeConfig appeal_signer_name might be configured; fall back to surgeon
        getattr(settings, "appeal_signer_name", None)
        or s.surgeon_primary
        or "Surgeon, MD"
    )
    story.append(Paragraph(f"<b>{signer_name}</b>", st["body"]))
    story.append(Paragraph(s.surgeon_primary or "", st["small"]))

    doc.build(story)
    return buf.getvalue()


def generate_modifier_22_letter(db: Session, s: Surgery, mod22_cpts: list[dict]) -> SurgeryFile:
    """Render the letter, save to disk under settings.export_dir, and create
    a SurgeryFile row of kind='modifier_22_letter'. Returns the row."""
    pdf_bytes = _render_pdf(s, mod22_cpts)

    out_dir = Path(settings.export_dir) / "modifier_22_letters"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"modifier_22_{s.chart_number or s.id[:8]}_{stamp}.pdf"
    fpath = out_dir / fname
    fpath.write_bytes(pdf_bytes)

    row = SurgeryFile(
        surgery_id=s.id,
        kind="modifier_22_letter",
        filename=fname,
        path=str(fpath),
        mime_type="application/pdf",
        size_bytes=len(pdf_bytes),
        uploaded_by="system:billing-ai",
        notes=f"Auto-generated justification for {len(mod22_cpts)} modifier-22 CPT(s).",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
