"""Generate a patient-facing benefits-estimate PDF.

Plain language. Shows the allowed amount, deductible/coinsurance/copay
breakdown, and the final patient responsibility. Includes the standard
"this is an estimate" disclaimer that all health-plan estimates need.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgeryFile

UPLOADS_DIR = "/Users/wwcclaudecode/Documents/wwc-era-project/backend/uploads/surgery_benefits_pdfs"
os.makedirs(UPLOADS_DIR, exist_ok=True)


# Brand colors
PLUM       = HexColor("#7B2D5E")
PLUM_LIGHT = HexColor("#F5E8EE")
INK        = HexColor("#2A1B23")
MUTED      = HexColor("#6B5560")
RULE       = HexColor("#E5D7DE")
GREEN      = HexColor("#0F8A4D")


def _money(v) -> str:
    if v is None:
        return "—"
    return f"${float(v):,.2f}"


def _proc_label(s: Surgery) -> str:
    parts = []
    for p in (s.procedures or []):
        d = (p.get("description") or "").strip()
        if d:
            parts.append(d)
    return " · ".join(parts) if parts else "your scheduled surgery"


def generate(s: Surgery, breakdown: dict) -> str:
    """Build the PDF and return the saved path. Caller persists the
    SurgeryFile row."""
    fname = f"benefits_{s.chart_number}_{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.pdf"
    out_path = os.path.join(UPLOADS_DIR, fname)

    doc = SimpleDocTemplate(out_path, pagesize=letter,
                             leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                             topMargin=0.5 * inch, bottomMargin=0.5 * inch)

    base = getSampleStyleSheet()["Normal"]
    h_style = ParagraphStyle("h", parent=base, fontName="Helvetica-Bold",
                              fontSize=20, textColor=PLUM, spaceAfter=2)
    sub_style = ParagraphStyle("sub", parent=base, fontName="Helvetica-Oblique",
                                fontSize=10, textColor=PLUM, spaceAfter=14)
    body = ParagraphStyle("body", parent=base, fontName="Helvetica",
                           fontSize=10, textColor=INK, leading=14)
    section = ParagraphStyle("section", parent=base, fontName="Helvetica-Bold",
                              fontSize=11, textColor=PLUM, spaceBefore=10,
                              spaceAfter=4)
    muted = ParagraphStyle("muted", parent=base, fontName="Helvetica",
                            fontSize=9, textColor=MUTED, leading=12)

    story = []

    # Header
    story.append(Paragraph("Waldorf Women's Care &amp; Aesthetics", h_style))
    story.append(Paragraph("Surgery Cost Estimate", sub_style))

    # Patient + procedure summary table
    today = date.today()
    rows = [
        ["Patient",   s.patient_name],
        ["Chart #",   s.chart_number],
    ]
    if s.dob:
        rows.append(["Date of birth", str(s.dob)])
    rows.append(["Procedure", _proc_label(s)])
    if s.primary_insurance:
        ins = s.primary_insurance
        if s.primary_member_id:
            ins += f"   (member ID: {s.primary_member_id})"
        rows.append(["Insurance", ins])
    rows.append(["Estimate prepared", today.strftime("%B %d, %Y")])

    summary = Table(rows, colWidths=[1.4 * inch, 5.6 * inch])
    summary.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (1, 0), (1, -1), INK),
        ("BACKGROUND", (0, 0), (-1, -1), PLUM_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(summary)

    # Big "you owe" box
    story.append(Spacer(1, 14))
    you_owe = float(breakdown.get("patient_responsibility") or 0)
    capped = breakdown.get("capped_by_oop_max")
    big_label = "Estimated patient responsibility"
    if capped:
        big_label += "  (capped by your annual out-of-pocket maximum)"
    big_table = Table([
        [Paragraph(big_label, ParagraphStyle("big_label", parent=base,
                  fontName="Helvetica-Bold", fontSize=11, textColor=PLUM)),
         Paragraph(f"<font size='22' color='#0F8A4D'><b>{_money(you_owe)}</b></font>",
                   base)],
    ], colWidths=[4.2 * inch, 2.8 * inch])
    big_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",  (1, 0), (1, 0), "RIGHT"),
        ("BACKGROUND", (0, 0), (-1, -1), PLUM_LIGHT),
        ("BOX", (0, 0), (-1, -1), 1, PLUM),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(big_table)

    # Breakdown table
    story.append(Paragraph("How we calculated this", section))
    breakdown_rows = [
        ["", "Amount", "Notes"],
        ["Insurance allowed amount", _money(s.allowed_amount),
         "What insurance considers reasonable for this procedure"],
        ["Annual deductible",        _money(s.deductible), ""],
        ["Deductible already met",   _money(s.deductible_met),
         f"Remaining: {_money(breakdown.get('deductible_remaining'))}"],
        ["Your deductible portion",  _money(breakdown.get('deductible_portion')),
         "What you pay before insurance shares the cost"],
        ["Coinsurance %",            (f"{float(s.coinsurance_pct):.0f}%" if s.coinsurance_pct else "—"),
         "Your share after deductible is met"],
        ["Coinsurance portion",      _money(breakdown.get('coinsurance_portion')),
         (f"{float(s.coinsurance_pct):.0f}% of {_money(breakdown.get('after_deductible'))}"
          if s.coinsurance_pct and breakdown.get('after_deductible') else "")],
        ["Copay",                    _money(s.copay),
         "Fixed visit copay" if (s.copay and float(s.copay) > 0) else ""],
        ["Annual out-of-pocket max", _money(s.oop_max),
         f"Already met: {_money(s.oop_met)}" if s.oop_met else ""],
        ["",                         "",  ""],
        ["Estimated responsibility", _money(you_owe), ""],
    ]
    breakdown_table = Table(breakdown_rows, colWidths=[2.4 * inch, 1.4 * inch, 3.2 * inch])
    breakdown_style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), PLUM_LIGHT),
        ("TEXTCOLOR", (0, 0), (-1, 0), PLUM),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, RULE),
        ("LINEABOVE", (0, -1), (-1, -1), 1, PLUM),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), PLUM_LIGHT),
        ("TEXTCOLOR", (0, -1), (-1, -1), PLUM),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
    ]
    breakdown_table.setStyle(TableStyle(breakdown_style))
    story.append(breakdown_table)

    # Plain-language explanation
    story.append(Paragraph("In plain language", section))
    explainer = build_plain_language(s, breakdown)
    story.append(Paragraph(explainer, body))

    # Payment instructions
    story.append(Paragraph("How to pay", section))
    story.append(Paragraph(
        "We collect the patient responsibility before scheduling the surgery date. "
        "Pay through your <b>ModMed Pay</b> patient portal, or call our office at "
        "<b>240-252-2140</b> to set up a payment plan.",
        body))

    # Disclaimer
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "<b>This is an estimate, not a bill.</b> Final amounts may differ based on "
        "actual services rendered, claim adjudication by your insurance, secondary "
        "coverage, and any updates to your benefits since this estimate was prepared. "
        "Surprise services or complications during surgery can change the amount due. "
        "Questions about your benefits? Call your insurance directly using the number "
        "on the back of your card.",
        muted))

    # Footer
    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "Waldorf Women's Care &amp; Aesthetics  ·  4470 Regency Place, Suite 106, "
        "White Plains, MD 20695  ·  240-252-2140  ·  info@waldorfwomenscare.com",
        muted))

    doc.build(story)
    return out_path


def build_plain_language(s: Surgery, b: dict) -> str:
    """Compose a 2–4 sentence plain-language explanation tailored to the
    actual numbers."""
    sentences = []

    proc = _proc_label(s)
    you_owe = float(b.get("patient_responsibility") or 0)
    sentences.append(f"For your <b>{proc}</b>, your estimated out-of-pocket cost is "
                     f"<b>{_money(you_owe)}</b>.")

    ded_remaining = float(b.get("deductible_remaining") or 0)
    ded_portion = float(b.get("deductible_portion") or 0)
    coins_portion = float(b.get("coinsurance_portion") or 0)
    copay = float(b.get("copay_portion") or 0)

    if ded_portion > 0:
        sentences.append(
            f"Of that, <b>{_money(ded_portion)}</b> goes toward your unmet annual "
            f"deductible. After your deductible is met, your insurance starts sharing "
            f"the cost of the procedure.")
    else:
        sentences.append(
            "You've already met your annual deductible — that means insurance is "
            "sharing the cost with you on this surgery.")

    if coins_portion > 0:
        coins_pct = float(s.coinsurance_pct or 0)
        sentences.append(
            f"After the deductible, you pay <b>{coins_pct:.0f}%</b> of the remaining "
            f"allowed amount, which works out to <b>{_money(coins_portion)}</b>.")
    if copay > 0:
        sentences.append(f"There is also a fixed copay of <b>{_money(copay)}</b>.")

    if b.get("capped_by_oop_max"):
        sentences.append(
            "Good news: this estimate has been <b>capped</b> at your remaining annual "
            "out-of-pocket maximum, so you won't owe more than that for the year.")

    return " ".join(sentences)


# ─── Public service entry ───────────────────────────────────────────

def generate_and_attach(db: Session, surgery: Surgery, breakdown: dict,
                         *, by_email: str) -> SurgeryFile:
    """Build the PDF and attach as a SurgeryFile (kind='benefits_estimate').
    If a previous estimate already exists, leave it (history) and add a new one."""
    path = generate(surgery, breakdown)
    f = SurgeryFile(
        surgery_id=surgery.id,
        kind="benefits_estimate",
        filename=os.path.basename(path),
        path=path,
        mime_type="application/pdf",
        size_bytes=os.path.getsize(path),
        notes=f"Estimate: ${breakdown.get('patient_responsibility'):.2f}",
        uploaded_by=by_email,
    )
    db.add(f)
    db.commit(); db.refresh(f)
    return f
