"""
Patient ledger / per-visit statement PDF generator.

Two flavors share the same letterhead and styling:

* generate_full_ledger_pdf(...)   — multi-visit account statement, all claims
                                    in the ledger window (default 5 years).
* generate_visit_statement_pdf(...) — single-visit statement, useful for
                                      patient questions or disputes.

Patient-facing language: "Insurance discount" replaces "contractual
adjustment" and similar billing jargon. Money trail per service line is
preserved (billed → adjustment → allowed → ins paid → pt paid → balance).
"""
from __future__ import annotations

import os
from datetime import date
from io import BytesIO
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image, KeepTogether, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle,
)

from app.config import settings


# ────────────────── Brand ──────────────────
PLUM = colors.HexColor("#6d28d9")
PLUM_DARK = colors.HexColor("#3b0764")
PLUM_LIGHT = colors.HexColor("#f3e8ff")
GRAY = colors.HexColor("#374151")
GRAY_LIGHT = colors.HexColor("#9ca3af")
GRAY_BG = colors.HexColor("#f9fafb")
GREEN = colors.HexColor("#15803d")
RED = colors.HexColor("#b91c1c")
AMBER_BG = colors.HexColor("#fef3c7")
BLUE_BG = colors.HexColor("#dbeafe")
BLUE_DARK = colors.HexColor("#1e40af")

LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "wwc-logo.png")


# ────────────────── Styles ──────────────────
def _styles():
    s = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("Title", parent=s["Heading1"], fontSize=14,
                                textColor=PLUM_DARK, alignment=TA_RIGHT,
                                fontName="Helvetica-Bold", spaceAfter=2),
        "addr": ParagraphStyle("Addr", parent=s["Normal"], fontSize=8,
                               textColor=GRAY_LIGHT, leading=10),
        "stmt_meta": ParagraphStyle("Meta", parent=s["Normal"], fontSize=8,
                                    textColor=GRAY_LIGHT, alignment=TA_RIGHT,
                                    leading=10),
        "section": ParagraphStyle("Section", parent=s["Heading2"], fontSize=9,
                                  textColor=GRAY, fontName="Helvetica-Bold",
                                  spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("Body", parent=s["Normal"], fontSize=9, leading=12),
        "small": ParagraphStyle("Small", parent=s["Normal"], fontSize=8,
                                textColor=GRAY_LIGHT, leading=10),
        "footer": ParagraphStyle("Footer", parent=s["Normal"], fontSize=7,
                                 textColor=GRAY_LIGHT, leading=9),
        "callout": ParagraphStyle("Callout", parent=s["Normal"], fontSize=9,
                                  textColor=BLUE_DARK, leading=12),
    }


def _money(v) -> str:
    """Render a positive money value. Treat 0 / None as '$0.00'."""
    try:
        f = float(v or 0)
        if f < 0:
            return f"−${-f:,.2f}"  # use real minus sign
        return f"${f:,.2f}"
    except Exception:
        return "$0.00"


def _neg(v) -> str:
    """Render a SUBTRACTED amount on a summary row. $0 stays '$0.00' (not −$0.00)."""
    try:
        f = float(v or 0)
        if f == 0:
            return "$0.00"
        if f < 0:
            return f"−${-f:,.2f}"
        return f"−${f:,.2f}"
    except Exception:
        return "$0.00"


def _money_or_dash(v) -> str:
    """For service-line columns where 0 should appear as a dash, not '$0.00'."""
    try:
        f = float(v or 0)
        if f == 0:
            return "—"
        return _money(f)
    except Exception:
        return "—"


def _date_str(d) -> str:
    if d is None:
        return ""
    if hasattr(d, "strftime"):
        return d.strftime("%m/%d/%Y")
    s = str(d).strip()
    # Many upstream callers stringify dates as ISO ("1996-05-10") before
    # handing them off. Convert to the MM/DD/YYYY convention so the PDF
    # doesn't leak the database format to the patient.
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            from datetime import datetime
            return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%m/%d/%Y")
        except ValueError:
            pass
    return s


# ────────────────── Letterhead (shared) ──────────────────
def _letterhead(styles, title_text: str, statement_id: Optional[str] = None) -> Table:
    """Two-column letterhead with logo + practice info on the left,
    statement title + metadata on the right."""

    practice_name = settings.practice_name
    practice_addr = settings.practice_address
    practice_phone = settings.practice_phone
    practice_npi = settings.practice_npi

    # Left: logo image (if available) over practice contact
    left_cells = []
    if os.path.exists(LOGO_PATH):
        try:
            logo = Image(LOGO_PATH, width=1.3 * inch, height=0.6 * inch, kind="proportional")
            left_cells.append(logo)
        except Exception:
            left_cells.append(Paragraph(f"<b>{practice_name}</b>",
                ParagraphStyle("PN", fontSize=14, textColor=PLUM_DARK,
                               fontName="Helvetica-Bold")))
    else:
        left_cells.append(Paragraph(f"<b>{practice_name}</b>",
            ParagraphStyle("PN", fontSize=14, textColor=PLUM_DARK,
                           fontName="Helvetica-Bold")))

    left_cells.append(Spacer(1, 4))
    left_cells.append(Paragraph(
        f"{practice_addr}<br/>"
        f"Phone: {practice_phone} &nbsp;·&nbsp; Fax: 240.252.2141<br/>"
        f"NPI: {practice_npi} &nbsp;·&nbsp; info@wwcgyn.com",
        styles["addr"],
    ))

    # Right: statement title + metadata
    right_cells = [Paragraph(title_text, styles["title"])]
    meta_parts = [f"Generated {date.today().strftime('%m/%d/%Y')}"]
    if statement_id:
        meta_parts.append(f"Statement #{statement_id}")
    right_cells.append(Paragraph(" · ".join(meta_parts), styles["stmt_meta"]))

    table = Table(
        [[left_cells, right_cells]],
        colWidths=[3.7 * inch, 3.6 * inch],
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _patient_block(ledger: dict, styles, period_label: str) -> Table:
    pat = ledger.get("patient") or {}
    full_name = pat.get("full_name") or "—"
    chart = pat.get("patient_id") or "—"
    dob = _date_str(pat.get("date_of_birth"))
    address = (pat.get("address") or "").strip()
    phone = (pat.get("phone") or "").strip()
    primary_ins = pat.get("primary_insurance") or ""

    addr_html = ""
    if address:
        # PrimeSuite stores address as a single string; render as-is, replacing
        # commas with line breaks for postcard-style display.
        parts = [p.strip() for p in address.split(",") if p.strip()]
        addr_html = "<br/>" + "<br/>".join(
            f"<font size=8 color='#6b7280'>{p}</font>" for p in parts
        )
    if phone:
        addr_html += f"<br/><font size=8 color='#9ca3af'>Phone</font> {phone}"

    left = Paragraph(
        f"<font size=7 color='#9ca3af'><b>BILL TO</b></font><br/>"
        f"<b>{full_name}</b>"
        f"{addr_html}",
        styles["body"],
    )
    right_lines = [
        ("Chart #", chart),
        ("Date of Birth", dob or "—"),
        ("Primary Insurance", primary_ins or "—"),
        ("Statement Period", period_label),
    ]
    right_html = "<br/>".join(
        f"<font size=7 color='#9ca3af'>{lbl.upper()}</font>&nbsp;&nbsp;{val}"
        for lbl, val in right_lines
    )
    right = Paragraph(right_html, ParagraphStyle("R", parent=styles["body"],
                                                 alignment=TA_RIGHT,
                                                 leading=14))

    table = Table([[left, right]], colWidths=[3.7 * inch, 3.6 * inch])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _visit_meta_box(dos_entry: dict, claim: dict, styles) -> Table:
    """Compact metadata box for a single visit's statement: DOS, provider,
    insurance, claim status."""
    rows = [
        ("Date of Service", _date_str(dos_entry.get("date_of_service"))),
        ("Visit / Claim #", claim.get("claim_number") or "—"),
        ("Provider", _short_provider(claim.get("rendering_provider_name") or "—")),
        ("Insurance", claim.get("payer_name") or "—"),
        ("Insurance Order", (claim.get("insurance_order") or "primary").title()),
        ("Status", (claim.get("status") or "—").replace("_", " ").title()),
    ]
    data = [
        [
            Paragraph(f"<font size=7 color='#9ca3af'>{lbl.upper()}</font><br/>{val}",
                      ParagraphStyle("VM", fontSize=9, leading=12)),
        ]
        for lbl, val in rows
    ]
    # Lay out as 2 columns
    pairs = [
        [data[0][0], data[1][0]],
        [data[2][0], data[3][0]],
        [data[4][0], data[5][0]],
    ]
    table = Table(pairs, colWidths=[3.5 * inch, 3.5 * inch])
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, GRAY_LIGHT),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, GRAY_LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 0), (-1, -1), GRAY_BG),
    ]))
    return table


# ────────────────── Public: full ledger PDF ──────────────────
def generate_full_ledger_pdf(ledger: dict) -> bytes:
    """Render the multi-visit patient account statement.

    `ledger` is the dict returned by `ledger_service.get_patient_ledger`.
    """
    styles = _styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=0.55 * inch, leftMargin=0.55 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )

    elements = []
    pat = ledger.get("patient") or {}
    statement_id = f"L-{date.today().strftime('%Y%m%d')}-{(pat.get('patient_id') or '0')}"
    period_label = _ledger_period_label(ledger)

    # Letterhead + divider
    elements.append(_letterhead(styles, "PATIENT ACCOUNT STATEMENT", statement_id))
    elements.append(HRFlowable(width="100%", thickness=2, color=PLUM, spaceAfter=10))

    # Patient block
    elements.append(_patient_block(ledger, styles, period_label))
    elements.append(Spacer(1, 6))

    # Account summary
    elements.append(_account_summary_table(ledger, styles))
    elements.append(Spacer(1, 8))

    # Optional callout if there's an account credit
    credit = _account_credit_total(ledger)
    if credit > 0:
        msg = (
            f"You have <b>{_money(credit)}</b> in on-account credit. "
            "It will be applied to your next visit unless you request a refund."
        )
        elements.append(_callout_box(msg, styles, color=BLUE_BG, text_color=BLUE_DARK))
        elements.append(Spacer(1, 6))

    # Visit detail table
    elements.append(Paragraph("VISIT DETAIL", styles["section"]))
    elements.append(_visit_detail_table(ledger, styles))
    elements.append(Spacer(1, 8))

    # Payment history (if any)
    if ledger.get("payment_history"):
        elements.append(Paragraph("PAYMENT HISTORY", styles["section"]))
        elements.append(_payment_history_table(ledger, styles))

    # Footer
    elements.append(Spacer(1, 18))
    elements.append(_footer_paragraph(styles))

    doc.build(elements)
    buffer.seek(0)
    return buffer.read()


# ────────────────── Public: per-visit statement ──────────────────
def generate_visit_statement_pdf(
    ledger: dict, visit_id: str, claim_data: Optional[dict] = None,
) -> bytes:
    """Render a single-visit (per-DOS) statement.

    Looks up `visit_id` (i.e. claim_number) in the ledger's dos_entries.
    `claim_data` may be passed to enrich detail (service lines).
    """
    styles = _styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=0.55 * inch, leftMargin=0.55 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
    elements = []

    # Find the matching claim/visit in the ledger
    matched_dos = None
    matched_claim = None
    for dos_entry in ledger.get("dos_entries", []):
        for claim in dos_entry.get("claims", []):
            if claim.get("claim_number") == visit_id or str(claim.get("claim_id")) == visit_id:
                matched_dos = dos_entry
                matched_claim = claim
                break
        if matched_claim:
            break

    pat = ledger.get("patient") or {}
    statement_id = f"V-{visit_id}-{date.today().strftime('%Y%m%d')}"

    elements.append(_letterhead(styles, "VISIT STATEMENT", statement_id))
    elements.append(HRFlowable(width="100%", thickness=2, color=PLUM, spaceAfter=10))

    period_label = (
        f"DOS {_date_str(matched_dos.get('date_of_service'))}"
        if matched_dos else "Visit not found"
    )
    elements.append(_patient_block(ledger, styles, period_label))
    elements.append(Spacer(1, 6))

    if not matched_claim:
        elements.append(Paragraph(
            f"No visit found matching <b>{visit_id}</b> in this patient's ledger.",
            styles["body"],
        ))
        doc.build(elements)
        buffer.seek(0)
        return buffer.read()

    # Visit metadata box (DOS, provider, insurance, status)
    elements.append(_visit_meta_box(matched_dos, matched_claim, styles))
    elements.append(Spacer(1, 8))

    # Visit summary box
    elements.append(_single_visit_summary(matched_dos, matched_claim, styles))
    elements.append(Spacer(1, 8))

    # Service-line breakdown
    elements.append(Paragraph("SERVICE DETAIL", styles["section"]))
    elements.append(_visit_service_lines_table(matched_claim, styles))
    elements.append(Spacer(1, 8))

    # Footer
    elements.append(Spacer(1, 18))
    elements.append(_footer_paragraph(styles))

    doc.build(elements)
    buffer.seek(0)
    return buffer.read()


# ────────────────── Helpers — tables ──────────────────
def _ledger_period_label(ledger: dict) -> str:
    entries = ledger.get("dos_entries") or []
    if not entries:
        return "—"
    first = _date_str(entries[0].get("date_of_service"))
    last = _date_str(entries[-1].get("date_of_service"))
    return f"{first} – {last}"


def _account_credit_total(ledger: dict) -> float:
    """Sum of patient on-account credit. Pulled from patient.credits if
    present (added later when Charge Analysis credit fields are ingested);
    otherwise 0."""
    pat = ledger.get("patient") or {}
    credits = pat.get("credits") or {}
    total = 0.0
    for k in ("insurance", "patient", "pre_pay", "undetermined"):
        try:
            total += float(credits.get(k) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _account_summary_table(ledger: dict, styles) -> Table:
    s = ledger.get("summary") or {}
    credit = _account_credit_total(ledger)
    open_balance = float(s.get("outstanding_balance") or 0)
    net_due = max(0.0, open_balance - credit)

    rows = [
        ["Total charges",          _money(s.get("total_billed"))],
        ["Insurance discount",     _neg(s.get("total_contractual_adjustment"))],
        ["Insurance payments",     _neg(s.get("total_insurance_paid"))],
        ["Your payments",          _neg(s.get("total_patient_paid"))],
        ["Open balance",           _money(open_balance)],
    ]
    if credit > 0:
        rows.append(["Less: on-account credit", _neg(credit)])
    rows.append(["NET AMOUNT DUE", _money(net_due)])

    table = Table(rows, colWidths=[5.5 * inch, 1.5 * inch])
    style = TableStyle([
        ("FONTNAME", (0, 0), (-1, -2), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -3), 0.5, GRAY_LIGHT),
        ("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), PLUM_LIGHT),
        ("FONTNAME", (0, len(rows) - 1), (-1, len(rows) - 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, len(rows) - 1), (-1, len(rows) - 1), 11),
        ("TEXTCOLOR", (0, len(rows) - 1), (-1, len(rows) - 1), PLUM_DARK),
        ("TOPPADDING", (0, len(rows) - 1), (-1, len(rows) - 1), 7),
        ("BOTTOMPADDING", (0, len(rows) - 1), (-1, len(rows) - 1), 7),
        ("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), 1, PLUM),
    ])
    if credit > 0:
        # Highlight credit row
        credit_row = len(rows) - 2
        style.add("BACKGROUND", (0, credit_row), (-1, credit_row), BLUE_BG)
        style.add("TEXTCOLOR", (0, credit_row), (-1, credit_row), BLUE_DARK)
    table.setStyle(style)
    return table


def _visit_detail_table(ledger: dict, styles) -> Table:
    header = ["Date", "Provider", "Service", "Charged", "Discount", "Ins paid", "You paid", "Balance"]
    data = [header]
    for entry in ledger.get("dos_entries", []):
        for claim in entry.get("claims", []):
            data.append([
                _date_str(entry.get("date_of_service")),
                _short_provider(claim.get("rendering_provider_name") or ""),
                _service_summary(claim),
                _money(claim.get("billed_amount")),
                _neg(claim.get("contractual_adjustment")),
                _neg(claim.get("paid_amount")),
                _neg(claim.get("patient_paid")),
                _money(claim.get("balance")),
            ])

    if len(data) == 1:
        data.append(["", "", "(no visits in this period)", "", "", "", "", ""])

    table = Table(data, colWidths=[
        0.85 * inch, 1.0 * inch, 1.85 * inch, 0.7 * inch, 0.7 * inch,
        0.7 * inch, 0.7 * inch, 0.7 * inch,
    ], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), GRAY_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRAY),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, GRAY_LIGHT),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRAY_BG]),
    ]))
    return table


def _payment_history_table(ledger: dict, styles) -> Table:
    header = ["Date", "Type", "From", "Method / Reference", "Amount"]
    data = [header]
    for p in ledger.get("payment_history", []):
        type_label = (p.get("type") or "").replace("_", " ").title()
        method = p.get("method") or ""
        ref = p.get("check_number") or p.get("receipt") or ""
        method_ref = " · ".join([x for x in [method, ref] if x])
        data.append([
            _date_str(p.get("date")),
            type_label,
            p.get("payer") or "—",
            method_ref or "—",
            _money(p.get("amount")),
        ])
    if len(data) == 1:
        data.append(["", "", "(no payments in this period)", "", ""])

    table = Table(data, colWidths=[
        0.95 * inch, 1.0 * inch, 1.75 * inch, 2.5 * inch, 1.0 * inch,
    ], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), GRAY_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRAY),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, GRAY_LIGHT),
        ("ALIGN", (4, 0), (4, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRAY_BG]),
    ]))
    return table


def _single_visit_summary(dos_entry: dict, claim: dict, styles) -> Table:
    """Summary box for a single visit's statement."""
    rows = [
        ["Total charges",              _money(claim.get("billed_amount"))],
        ["Insurance discount",         _neg(claim.get("contractual_adjustment"))],
        ["Insurance payment",          _neg(claim.get("paid_amount"))],
        ["Your payments to date",      _neg(claim.get("patient_paid"))],
        ["BALANCE DUE FOR THIS VISIT", _money(claim.get("balance"))],
    ]
    table = Table(rows, colWidths=[5.5 * inch, 1.5 * inch])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, GRAY_LIGHT),
        ("BACKGROUND", (0, -1), (-1, -1), PLUM_LIGHT),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (-1, -1), 11),
        ("TEXTCOLOR", (0, -1), (-1, -1), PLUM_DARK),
        ("TOPPADDING", (0, -1), (-1, -1), 7),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
        ("LINEABOVE", (0, -1), (-1, -1), 1, PLUM),
    ]))
    return table


def _visit_service_lines_table(claim: dict, styles) -> Table:
    header = ["CPT", "Description", "Units", "Charged", "Discount", "Ins paid", "Pt resp", "Pt paid"]
    data = [header]
    for sl in claim.get("service_lines", []):
        billed = float(sl.get("billed_amount") or 0)
        paid = float(sl.get("paid_amount") or 0)
        pt_resp = float(sl.get("patient_responsibility") or 0)
        discount = max(0, billed - paid - pt_resp)
        data.append([
            sl.get("procedure_code") or "—",
            (sl.get("description") or "")[:42],
            f"{float(sl.get('units') or 1):g}",
            _money(billed),
            _neg(discount),
            _neg(paid),
            _money(pt_resp),
            "—",  # patient-paid per service-line not tracked yet
        ])
    if len(data) == 1:
        data.append(["", "(no service lines)", "", "", "", "", "", ""])

    table = Table(data, colWidths=[
        0.7 * inch, 2.0 * inch, 0.5 * inch, 0.7 * inch, 0.7 * inch,
        0.7 * inch, 0.7 * inch, 0.7 * inch,
    ], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), GRAY_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), GRAY),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, GRAY_LIGHT),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRAY_BG]),
    ]))
    return table


def _callout_box(html: str, styles, color, text_color) -> Table:
    para = Paragraph(html, ParagraphStyle("Callout", parent=styles["body"],
                                           fontSize=9, textColor=text_color,
                                           leading=12))
    t = Table([[para]], colWidths=[7.0 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _footer_paragraph(styles) -> Paragraph:
    practice_phone = settings.practice_phone
    return Paragraph(
        f"<b>Questions about your bill?</b> Call our billing office at {practice_phone} "
        "between 9 AM and 5 PM, Monday through Friday. Insurance payments may take 30–45 days "
        "to post after your visit. Please retain this statement for your records. "
        "Charges and payments may continue to appear after this statement date for "
        "services already rendered.",
        styles["footer"],
    )


def _short_provider(name: str) -> str:
    """'Cooke, Aryian MD' → 'Cooke, A. MD'"""
    if not name or "," not in name:
        return name or "—"
    last, rest = name.split(",", 1)
    rest_parts = rest.strip().split()
    if not rest_parts:
        return last
    first = rest_parts[0]
    title = " ".join(rest_parts[1:]) if len(rest_parts) > 1 else ""
    return f"{last.strip()}, {first[0]}. {title}".strip()


def _service_summary(claim: dict) -> str:
    sls = claim.get("service_lines") or []
    if not sls:
        return claim.get("payer_name") or ""
    if len(sls) == 1:
        sl = sls[0]
        desc = (sl.get("description") or sl.get("procedure_code") or "").strip()
        return desc[:30]
    codes = ", ".join(s.get("procedure_code") or "" for s in sls[:3] if s.get("procedure_code"))
    if len(sls) > 3:
        codes += f" +{len(sls) - 3}"
    return codes
