"""Generate a patient-facing Patient Responsibility Estimate PDF.

Branded with the WWC logo, consistent with the surgery-portal aesthetic.
Saved through the storage adapter (save_blob) so it works in GCS on
Cloud Run. Surfaced to the patient on the portal under
Instructions & Documents.
"""
from __future__ import annotations

import io
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgeryFile
from app.services.storage import save_blob


_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "assets")
LOGO_PATH = os.path.normpath(os.path.join(_ASSETS_DIR, "wwc-logo.png"))


# Brand colors — match the portal's plum palette.
PLUM       = HexColor("#7B2D5E")
PLUM_DARK  = HexColor("#4C1D40")
PLUM_INK   = HexColor("#2A0E25")
PLUM_LIGHT = HexColor("#F5E8EE")
PLUM_50    = HexColor("#FAF3F7")
INK        = HexColor("#2A1B23")
MUTED      = HexColor("#6B5560")
RULE       = HexColor("#E5D7DE")
GREEN      = HexColor("#0F8A4D")
ROSE       = HexColor("#9F1239")


def _money(v) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return "—"


def _proc_label(s: Surgery) -> str:
    parts = []
    for p in (s.procedures or []):
        d = (p.get("description") or "").strip()
        if d:
            parts.append(d)
    return " · ".join(parts) if parts else "your scheduled surgery"


def _manual_payments(s: Surgery) -> list[dict]:
    """Return only the manual offsets so the PDF can list them."""
    out = []
    for p in (s.payments or []):
        if p.status == "paid" and (p.kind or "") == "manual_offset":
            out.append({
                "paid_at":     p.paid_at.strftime("%m/%d/%Y") if p.paid_at else "",
                "amount":      float(p.amount_paid or 0),
                "description": p.description or "",
            })
    out.sort(key=lambda r: r["paid_at"])
    return out


def generate_bytes(s: Surgery, breakdown: dict) -> bytes:
    """Build the PDF entirely in memory and return the bytes. Saving is
    handled by the caller via save_blob so we work on both local disk and
    GCS (Cloud Run) without a hardcoded path."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                             leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                             topMargin=0.4 * inch, bottomMargin=0.5 * inch)

    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle("body", parent=base, fontName="Helvetica",
                           fontSize=10, textColor=INK, leading=14)
    section = ParagraphStyle("section", parent=base, fontName="Helvetica-Bold",
                              fontSize=11, textColor=PLUM, spaceBefore=12,
                              spaceAfter=4)
    muted = ParagraphStyle("muted", parent=base, fontName="Helvetica",
                            fontSize=9, textColor=MUTED, leading=12)
    brand_eyebrow = ParagraphStyle("eyebrow", parent=base, fontName="Helvetica-Bold",
                                    fontSize=8, textColor=PLUM,
                                    leading=10, spaceAfter=0)
    title_h = ParagraphStyle("title_h", parent=base, fontName="Helvetica-Bold",
                              fontSize=22, textColor=PLUM_INK, spaceAfter=2,
                              leading=24)
    title_sub = ParagraphStyle("title_sub", parent=base,
                                fontName="Helvetica-Oblique", fontSize=10,
                                textColor=MUTED, spaceAfter=14)

    story = []

    # ─── Branded header ─────────────────────────────────────────────
    # WWC logo is ~1.93:1 — keep aspect ratio so it isn't warped.
    logo = None
    if os.path.exists(LOGO_PATH):
        try:
            logo_w = 1.5 * inch
            logo_h = logo_w / 1.932
            logo = Image(LOGO_PATH, width=logo_w, height=logo_h)
        except Exception:
            logo = None

    title_cell = [
        Paragraph("WALDORF WOMEN'S CARE", brand_eyebrow),
        Paragraph("Patient Responsibility Estimate", title_h),
        Paragraph("Prepared "
                  + date.today().strftime("%B %d, %Y"), title_sub),
    ]
    header_tbl = Table(
        [[logo or "", title_cell]],
        colWidths=[1.7 * inch, 5.4 * inch])
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 6))
    story.append(Table([["", ""]], colWidths=[7.1 * inch, 0],
                        style=TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1.2, PLUM)])))

    # ─── Patient summary ────────────────────────────────────────────
    rows = [["Patient",   s.patient_name or "—"]]
    if s.chart_number:
        rows.append(["Chart #", s.chart_number])
    if s.dob:
        rows.append(["Date of birth", s.dob.strftime("%m/%d/%Y")])
    rows.append(["Procedure", _proc_label(s)])
    if s.primary_insurance:
        ins = s.primary_insurance
        if s.primary_member_id:
            ins += f"   (member ID: {s.primary_member_id})"
        rows.append(["Primary insurance", ins])
    if s.secondary_insurance:
        sec = s.secondary_insurance
        if s.secondary_member_id:
            sec += f"   (member ID: {s.secondary_member_id})"
        rows.append(["Secondary insurance", sec])

    summary = Table(rows, colWidths=[1.5 * inch, 5.6 * inch])
    summary.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (1, 0), (1, -1), INK),
        ("BACKGROUND", (0, 0), (-1, -1), PLUM_50),
        ("BOX", (0, 0), (-1, -1), 0.4, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(Spacer(1, 10))
    story.append(summary)

    # ─── Big "you owe" callout ──────────────────────────────────────
    you_owe = float(breakdown.get("patient_responsibility") or 0)
    manual_paid = sum(p["amount"] for p in _manual_payments(s))
    net_owe = max(0.0, you_owe - manual_paid)
    capped = breakdown.get("capped_by_oop_max")

    big_label = "Your estimated responsibility"
    if capped:
        big_label += "  (capped by your annual out-of-pocket maximum)"
    big_table = Table([
        [Paragraph(big_label, ParagraphStyle("big_label", parent=base,
                  fontName="Helvetica-Bold", fontSize=11, textColor=PLUM_INK)),
         Paragraph(f"<font size='22' color='#0F8A4D'><b>{_money(net_owe)}</b></font>",
                   base)],
    ], colWidths=[4.4 * inch, 2.7 * inch])
    big_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",  (1, 0), (1, 0), "RIGHT"),
        ("BACKGROUND", (0, 0), (-1, -1), PLUM_LIGHT),
        ("BOX", (0, 0), (-1, -1), 1.2, PLUM),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(Spacer(1, 12))
    story.append(big_table)

    if manual_paid > 0:
        story.append(Paragraph(
            f"<font color='#6B5560' size='9'>"
            f"Pre-procedure estimate before credits: <b>{_money(you_owe)}</b>. "
            f"Already paid: <b>{_money(manual_paid)}</b>.</font>",
            body))

    # ─── Primary insurance breakdown ────────────────────────────────
    story.append(Paragraph("How your primary insurance applies", section))
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
        ["Owed after primary",       _money(breakdown.get("primary_patient_owed")), ""],
    ]
    breakdown_table = Table(breakdown_rows, colWidths=[2.4 * inch, 1.4 * inch, 3.3 * inch])
    breakdown_style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), PLUM_LIGHT),
        ("TEXTCOLOR", (0, 0), (-1, 0), PLUM_INK),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, RULE),
        ("LINEABOVE", (0, -1), (-1, -1), 1, PLUM),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), PLUM_LIGHT),
        ("TEXTCOLOR", (0, -1), (-1, -1), PLUM_INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
    ]
    breakdown_table.setStyle(TableStyle(breakdown_style))
    story.append(breakdown_table)

    # ─── Secondary insurance breakdown (when present) ───────────────
    secondary = breakdown.get("secondary")
    if secondary and s.secondary_insurance:
        story.append(Paragraph("How your secondary insurance applies", section))
        sec_rows = [
            ["", "Amount", "Notes"],
            ["Amount sent to secondary", _money(breakdown.get("primary_patient_owed")),
             "What was left after primary processed"],
            ["Secondary annual deductible", _money(s.secondary_deductible), ""],
            ["Secondary deductible met",    _money(s.secondary_deductible_met),
             f"Remaining: {_money(secondary.get('deductible_remaining'))}"],
            ["Secondary deductible portion", _money(secondary.get("deductible_portion")), ""],
            ["Secondary coinsurance %",
             (f"{float(s.secondary_coinsurance_pct):.0f}%"
              if s.secondary_coinsurance_pct else "—"), ""],
            ["Secondary coinsurance portion", _money(secondary.get("coinsurance_portion")), ""],
            ["Secondary copay", _money(s.secondary_copay), ""],
            ["Secondary OOP max", _money(s.secondary_oop_max),
             f"Already met: {_money(s.secondary_oop_met)}" if s.secondary_oop_met else ""],
            ["", "", ""],
            ["Owed after both insurances", _money(breakdown.get("patient_responsibility")), ""],
        ]
        sec_table = Table(sec_rows, colWidths=[2.4 * inch, 1.4 * inch, 3.3 * inch])
        sec_table.setStyle(TableStyle(breakdown_style))
        story.append(sec_table)

    # ─── Manual payments section ────────────────────────────────────
    manuals = _manual_payments(s)
    if manuals:
        story.append(Paragraph("Payments already received", section))
        rows_m = [["Date", "Amount", "Description"]]
        for p in manuals:
            rows_m.append([p["paid_at"], _money(p["amount"]), p["description"]])
        rows_m.append(["", _money(manual_paid), "Total credits"])
        mt = Table(rows_m, colWidths=[1.2 * inch, 1.2 * inch, 4.7 * inch])
        mt.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), PLUM_LIGHT),
            ("TEXTCOLOR", (0, 0), (-1, 0), PLUM_INK),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, GREEN),
            ("TEXTCOLOR", (0, -1), (-1, -1), GREEN),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BOX", (0, 0), (-1, -1), 0.5, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(mt)

    # ─── How to pay ────────────────────────────────────────────────
    story.append(Paragraph("How to pay", section))
    pay_msg = (
        "Pay through your <b>Waldorf Women's Care surgery portal</b>, ModMed Pay, "
        "or call our office at <b>240-252-2140</b> to set up a payment plan.")
    if s.card_on_file:
        pay_msg += (" Your card on file will be used for the final balance unless "
                     "you let us know otherwise.")
    story.append(Paragraph(pay_msg, body))

    # ─── Disclaimer ────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "<b>This is an estimate, not a bill.</b> Final amounts may differ based on "
        "actual services rendered, claim adjudication by your insurance, secondary "
        "coverage, and any updates to your benefits since this estimate was prepared. "
        "Surprise services or complications during surgery can change the amount due. "
        "Questions about your benefits? Call your insurance directly using the number "
        "on the back of your card.",
        muted))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Waldorf Women's Care &amp; Aesthetics  ·  240-252-2140  ·  "
        "surgery@waldorfwomenscare.com",
        muted))

    doc.build(story)
    return buf.getvalue()


# ─── Public service entry ───────────────────────────────────────────

def generate_and_attach(db: Session, surgery: Surgery, breakdown: dict,
                         *, by_email: str) -> SurgeryFile:
    """Build the PDF and attach as a SurgeryFile (kind='benefits_estimate').
    Persists through the storage adapter so the file works on Cloud Run + GCS."""
    pdf_bytes = generate_bytes(surgery, breakdown)
    fname = (f"patient_responsibility_estimate_{surgery.chart_number}_"
              f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.pdf")
    key = save_blob(prefix="surgery-benefits-pdfs",
                     body=pdf_bytes,
                     filename=fname)

    f = SurgeryFile(
        surgery_id=surgery.id,
        kind="benefits_estimate",
        filename=fname,
        path=key,
        mime_type="application/pdf",
        size_bytes=len(pdf_bytes),
        notes=(f"Patient Responsibility Estimate: "
                f"${float(breakdown.get('patient_responsibility') or 0):.2f}"),
        uploaded_by=by_email,
    )
    db.add(f)
    db.commit(); db.refresh(f)
    return f


# Backwards compat — older callers imported `generate(...)`. Now returns the
# bytes path-less so we don't reintroduce the hardcoded Mac uploads dir.
def generate(s: Surgery, breakdown: dict) -> bytes:
    return generate_bytes(s, breakdown)


def build_plain_language(s: Surgery, b: dict) -> str:
    """Retained for callers that surface a plain-language explanation
    alongside the PDF — used by the staff PDF preview at /benefits."""
    sentences = []
    proc = _proc_label(s)
    you_owe = float(b.get("patient_responsibility") or 0)
    sentences.append(f"For your <b>{proc}</b>, your estimated out-of-pocket cost is "
                     f"<b>{_money(you_owe)}</b>.")
    ded_portion = float(b.get("deductible_portion") or 0)
    coins_portion = float(b.get("coinsurance_portion") or 0)
    copay = float(b.get("copay_portion") or 0)
    if ded_portion > 0:
        sentences.append(
            f"<b>{_money(ded_portion)}</b> goes toward your unmet annual deductible.")
    if coins_portion > 0:
        coins_pct = float(s.coinsurance_pct or 0)
        sentences.append(
            f"You pay <b>{coins_pct:.0f}%</b> coinsurance after the deductible — "
            f"<b>{_money(coins_portion)}</b>.")
    if copay > 0:
        sentences.append(f"There is also a fixed copay of <b>{_money(copay)}</b>.")
    if b.get("secondary"):
        sentences.append(
            "Your secondary insurance further reduces this amount, as shown above.")
    return " ".join(sentences)
