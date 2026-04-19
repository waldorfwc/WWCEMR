"""
EOB (Explanation of Benefits) PDF Generator.
Produces a professional, patient-readable PDF using ReportLab.
"""

import os
from datetime import date
from decimal import Decimal
from typing import Optional
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether,
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.platypus import Image

from app.config import settings


# Brand colors
PRIMARY = colors.HexColor("#1B4F8A")
SECONDARY = colors.HexColor("#2E7D32")
ACCENT = colors.HexColor("#F57C00")
LIGHT_GRAY = colors.HexColor("#F5F5F5")
MED_GRAY = colors.HexColor("#9E9E9E")
DENIED_RED = colors.HexColor("#C62828")
PAID_GREEN = colors.HexColor("#2E7D32")


def _currency(val) -> str:
    try:
        return f"${float(val):,.2f}"
    except Exception:
        return "$0.00"


def generate_eob_pdf(
    claim_data: dict,
    service_lines: list[dict],
    adjustments: list[dict],
    patient_info: dict,
    practice_info: dict,
    output_path: Optional[str] = None,
) -> bytes:
    """
    Generate an EOB PDF.

    claim_data: dict with claim fields
    service_lines: list of service line dicts
    adjustments: list of adjustment dicts (carc code, description, amount, group)
    patient_info: dict with patient name, DOB, ID
    practice_info: dict with practice name, address, phone, NPI
    output_path: if provided, also save to disk
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "Title", parent=styles["Heading1"],
        fontSize=20, textColor=PRIMARY, spaceAfter=2,
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"],
        fontSize=10, textColor=MED_GRAY, alignment=TA_CENTER, spaceAfter=12,
    )
    section_header = ParagraphStyle(
        "SectionHeader", parent=styles["Heading2"],
        fontSize=11, textColor=PRIMARY, spaceBefore=12, spaceAfter=4,
        borderPad=4,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=9, leading=13,
    )
    small_style = ParagraphStyle(
        "Small", parent=styles["Normal"], fontSize=8, textColor=MED_GRAY,
    )
    bold_style = ParagraphStyle(
        "Bold", parent=styles["Normal"], fontSize=9, fontName="Helvetica-Bold",
    )
    right_style = ParagraphStyle(
        "Right", parent=styles["Normal"], fontSize=9, alignment=TA_RIGHT,
    )

    elements = []

    # ── Header ──────────────────────────────────────────────────────────
    practice_name = practice_info.get("name", settings.practice_name)
    header_data = [
        [
            Paragraph(f"<b>{practice_name}</b>", ParagraphStyle("PH", fontSize=14, textColor=PRIMARY, fontName="Helvetica-Bold")),
            Paragraph("<b>EXPLANATION OF BENEFITS</b>", ParagraphStyle("EOB", fontSize=16, textColor=PRIMARY, fontName="Helvetica-Bold", alignment=TA_RIGHT)),
        ],
        [
            Paragraph(
                f"{practice_info.get('address', '')}<br/>"
                f"Phone: {practice_info.get('phone', '')} | NPI: {practice_info.get('npi', '')}",
                small_style,
            ),
            Paragraph(
                f"Date Issued: <b>{date.today().strftime('%m/%d/%Y')}</b>",
                ParagraphStyle("DateR", fontSize=9, alignment=TA_RIGHT),
            ),
        ],
    ]
    header_table = Table(header_data, colWidths=[3.5 * inch, 3.8 * inch])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(header_table)
    elements.append(HRFlowable(width="100%", thickness=2, color=PRIMARY, spaceAfter=8))

    # ── Status Banner ───────────────────────────────────────────────────
    status = claim_data.get("status", "").lower()
    status_color = PAID_GREEN if status == "paid" else (DENIED_RED if status == "denied" else ACCENT)
    status_label = status.upper().replace("_", " ")
    banner = Table(
        [[Paragraph(f"<b>CLAIM STATUS: {status_label}</b>",
                    ParagraphStyle("Banner", fontSize=13, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER))]],
        colWidths=[7.3 * inch],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), status_color),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [status_color]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("ROUNDEDCORNERS", [4]),
    ]))
    elements.append(banner)
    elements.append(Spacer(1, 10))

    # ── Patient & Claim Info ─────────────────────────────────────────────
    dos_from = claim_data.get("date_of_service_from", "")
    dos_to = claim_data.get("date_of_service_to", "")
    dos_str = str(dos_from) if dos_from else ""
    if dos_to and dos_to != dos_from:
        dos_str += f" – {dos_to}"

    info_data = [
        ["PATIENT INFORMATION", "", "CLAIM INFORMATION", ""],
        ["Patient Name:", patient_info.get("full_name", ""), "Claim Number:", claim_data.get("claim_number", "")],
        ["Date of Birth:", patient_info.get("date_of_birth", ""), "Date of Service:", dos_str],
        ["Insurance ID:", claim_data.get("subscriber_id", ""), "Check Number:", claim_data.get("check_number", "N/A")],
        ["Insurance:", claim_data.get("payer_name", ""), "Check Date:", str(claim_data.get("check_date", "N/A"))],
        ["Group #:", claim_data.get("group_number", ""), "Provider:", claim_data.get("rendering_provider_name", "")],
    ]

    info_table = Table(info_data, colWidths=[1.5 * inch, 2 * inch, 1.5 * inch, 2.3 * inch])
    info_style = TableStyle([
        ("BACKGROUND", (0, 0), (1, 0), LIGHT_GRAY),
        ("BACKGROUND", (2, 0), (3, 0), LIGHT_GRAY),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), PRIMARY),
        ("SPAN", (0, 0), (1, 0)),
        ("SPAN", (2, 0), (3, 0)),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 1), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ])
    info_table.setStyle(info_style)
    elements.append(info_table)
    elements.append(Spacer(1, 12))

    # ── Service Lines ────────────────────────────────────────────────────
    elements.append(Paragraph("SERVICE DETAILS", section_header))

    svc_headers = ["Procedure", "Description / Modifier", "DOS", "Units", "Billed", "Allowed", "Paid", "Pt. Resp."]
    svc_rows = [svc_headers]
    for svc in service_lines:
        mods = " ".join(filter(None, [svc.get("modifier_1"), svc.get("modifier_2"), svc.get("modifier_3"), svc.get("modifier_4")]))
        desc = svc.get("description") or svc.get("procedure_code", "")
        if mods:
            desc = f"{desc} [{mods}]"
        svc_rows.append([
            svc.get("procedure_code", ""),
            Paragraph(desc[:60], small_style),
            str(svc.get("date_of_service_from", dos_str))[:10],
            str(svc.get("units", "1")),
            _currency(svc.get("billed_amount", 0)),
            _currency(svc.get("allowed_amount", 0)),
            _currency(svc.get("paid_amount", 0)),
            _currency(svc.get("patient_responsibility", 0)),
        ])

    svc_col_widths = [0.8*inch, 2.2*inch, 0.8*inch, 0.45*inch, 0.75*inch, 0.75*inch, 0.75*inch, 0.75*inch]
    svc_table = Table(svc_rows, colWidths=svc_col_widths)
    svc_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (3, 0), (3, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(svc_table)
    elements.append(Spacer(1, 10))

    # ── Adjustments ──────────────────────────────────────────────────────
    relevant_adj = [a for a in adjustments if a.get("group_code") != "CO" or a.get("reason_code") != "45"]
    if relevant_adj:
        elements.append(Paragraph("ADJUSTMENTS & REASON CODES", section_header))
        adj_headers = ["Group", "Code", "Description", "Amount"]
        adj_rows = [adj_headers]
        for adj in relevant_adj:
            group_labels = {"CO": "Contractual", "PR": "Patient Resp.", "OA": "Other Adj.", "PI": "Payer Init.", "CR": "Correction"}
            group_label = group_labels.get(adj.get("group_code", ""), adj.get("group_code", ""))
            adj_rows.append([
                group_label,
                adj.get("reason_code", ""),
                Paragraph(adj.get("reason_description", "")[:80], small_style),
                _currency(adj.get("amount", 0)),
            ])
        adj_table = Table(adj_rows, colWidths=[1.1*inch, 0.6*inch, 4.5*inch, 1.0*inch])
        adj_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#455A64")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (3, 0), (3, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(adj_table)
        elements.append(Spacer(1, 10))

    # ── Financial Summary ────────────────────────────────────────────────
    elements.append(Paragraph("FINANCIAL SUMMARY", section_header))

    billed = float(claim_data.get("billed_amount", 0))
    allowed = float(claim_data.get("allowed_amount", 0))
    contractual = float(claim_data.get("contractual_adjustment", 0))
    paid = float(claim_data.get("paid_amount", 0))
    patient_resp = float(claim_data.get("patient_responsibility", 0))
    other_adj = float(claim_data.get("other_adjustment", 0))
    balance = float(claim_data.get("balance", 0))

    summary_rows = [
        ["Total Billed Amount:", _currency(billed)],
        ["Contractual Adjustment (CO-45):", f"({_currency(contractual)})"],
        ["Allowed Amount:", _currency(allowed)],
        ["Insurance Payment:", _currency(paid)],
        ["Other Adjustments:", f"({_currency(other_adj)})"],
        ["Patient Responsibility:", _currency(patient_resp)],
        ["", ""],
        ["PATIENT BALANCE DUE:", _currency(balance)],
    ]

    summary_table = Table(summary_rows, colWidths=[4.5 * inch, 2.0 * inch])
    summary_style = TableStyle([
        ("FONTNAME", (0, 0), (-1, -2), "Helvetica"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.5, PRIMARY),
        ("TEXTCOLOR", (0, -1), (-1, -1), PRIMARY),
        ("FONTSIZE", (0, -1), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (0, -1), 6),
    ])
    summary_table.setStyle(summary_style)

    # Right-align summary
    outer = Table([[Spacer(1, 1), summary_table]], colWidths=[1.0 * inch, 6.5 * inch])
    elements.append(outer)
    elements.append(Spacer(1, 14))

    # ── Patient Balance Box ──────────────────────────────────────────────
    if balance > 0:
        balance_box = Table(
            [[Paragraph(
                f"<b>Amount Due from Patient: {_currency(balance)}</b><br/>"
                "<font size='8'>Please contact our billing office with any questions.</font>",
                ParagraphStyle("BalBox", fontSize=11, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER),
            )]],
            colWidths=[7.3 * inch],
        )
        balance_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        elements.append(balance_box)
        elements.append(Spacer(1, 10))

    # ── Denial Notice ────────────────────────────────────────────────────
    denials = claim_data.get("denials", [])
    if denials:
        elements.append(Paragraph("DENIAL INFORMATION & APPEAL RIGHTS", section_header))
        for d in denials:
            denial_box_data = [[
                Paragraph(
                    f"<b>Denial Reason:</b> [{d.get('carc_code', '')}] {d.get('carc_description', '')}<br/>"
                    f"<b>Amount Denied:</b> {_currency(d.get('denied_amount', 0))}<br/>"
                    f"<b>Appeal Deadline:</b> {d.get('appeal_deadline', 'Contact payer')}<br/>"
                    f"<b>Your Rights:</b> You have the right to appeal this decision. Contact our billing office for assistance.<br/>"
                    f"<b>Maryland Insurance Administration:</b> 800-492-6116 | insurance.maryland.gov",
                    ParagraphStyle("DenialText", fontSize=9, leading=13),
                )
            ]]
            denial_box = Table(denial_box_data, colWidths=[7.3 * inch])
            denial_box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFEBEE")),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("BOX", (0, 0), (-1, -1), 1, DENIED_RED),
            ]))
            elements.append(denial_box)
            elements.append(Spacer(1, 6))

    # ── Footer ──────────────────────────────────────────────────────────
    elements.append(Spacer(1, 10))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=MED_GRAY))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        f"This Explanation of Benefits is not a bill. Questions? Contact {practice_name} billing at "
        f"{practice_info.get('phone', '')}. "
        f"This document contains protected health information (PHI) and is confidential.",
        small_style,
    ))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)

    return pdf_bytes
