"""Generate a daily pellet count PDF for DEA-grade perpetual inventory.

Called from finish_count after a count is reconciled. The PDF captures:
  • Location, date, scope
  • Started_by + witness_at_start
  • Finished_by + witness_at_finish
  • Per-lot line (qualgen lot #, expiration, expected, counted, variance, notes)
  • Sign-off block for printed names + signatures (DEA practice)

Saved under uploads/pellet_counts/ and registered as a PelletCountAttachment.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Tuple

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether,
)
from sqlalchemy.orm import Session, joinedload

from app.models.pellet import PelletCount, PelletCountLine, PelletLot
from app.services.pellet_pdf_common import (
    PLUM, PLUM_LIGHT, INK, MUTED, RULE, GREEN, AMBER, RED, WHITE,
    LOC_LABEL as _LOC_LABEL,
    build_styles, fmt_ts as _ts, header_block, meta_table, footer_line,
)


UPLOADS_DIR = "/Users/wwcclaudecode/Documents/wwc-era-project/backend/uploads/pellet_counts"
os.makedirs(UPLOADS_DIR, exist_ok=True)


def generate_count_pdf(db: Session, count: PelletCount) -> Tuple[str, str, int]:
    """Render the PDF for `count`. Returns (path, filename, size_bytes)."""
    # Re-fetch with joined lot+dose_type so labels render even if caller
    # forgot to eagerly load.
    count = (db.query(PelletCount)
                .options(joinedload(PelletCount.lines)
                            .joinedload(PelletCountLine.lot)
                            .joinedload(PelletLot.dose_type))
                .filter(PelletCount.id == count.id).first())

    started_date = count.started_at.strftime("%Y%m%d") if count.started_at else "nodate"
    loc_slug = count.location or "loc"
    fname = (f"pellet-count_{loc_slug}_{started_date}_"
             f"{datetime.utcnow().strftime('%H%M%S')}.pdf")
    out_path = os.path.join(UPLOADS_DIR, fname)

    doc = SimpleDocTemplate(out_path, pagesize=letter,
                             leftMargin=0.55 * inch, rightMargin=0.55 * inch,
                             topMargin=0.5 * inch, bottomMargin=0.5 * inch)

    styles = build_styles()
    section = styles["section"]; body = styles["body"]; muted = styles["muted"]

    story: list = []
    story.extend(header_block("Daily Pellet Count — Perpetual Inventory Record", styles))

    # ── Meta box ──
    meta_rows = [
        ["Location",  _LOC_LABEL.get(count.location, count.location or "—")],
        ["Date",      count.started_at.strftime("%B %d, %Y") if count.started_at else "—"],
        ["Scope",     "Sch III only" if count.scope == "controlled_only" else "All lots"],
        ["Status",    (count.status or "").upper()],
        ["Started",   f"{_ts(count.started_at)} · {count.started_by or '—'}"],
        ["Witness (start)", count.witness_user_start or "—"],
        ["Finished",  (f"{_ts(count.finished_at)} · {count.finished_by or '—'}"
                        if count.finished_at else "—")],
        ["Witness (finish)", count.witness_user or "—"],
    ]
    story.append(meta_table(meta_rows))
    if count.notes:
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"<b>Notes:</b> {count.notes}", body))
    story.append(Spacer(1, 10))

    # ── Lines table ──
    story.append(Paragraph("Count detail (per lot)", section))

    header = ["Dose", "Lot #", "Expiration", "Expected", "Counted", "Variance", "Notes"]
    rows: list[list] = [header]
    total_expected = 0
    total_counted = 0
    total_variance_abs = 0
    has_variance = False

    lines = sorted(
        (count.lines or []),
        key=lambda l: ((l.lot.dose_type.label if l.lot and l.lot.dose_type else ""),
                        (l.lot.qualgen_lot_number if l.lot else "")),
    )
    for line in lines:
        lot = line.lot
        dose_label = lot.dose_type.label if lot and lot.dose_type else "—"
        qg_lot = lot.qualgen_lot_number if lot else "—"
        exp = str(lot.expiration_date) if lot and lot.expiration_date else "—"
        expected = int(line.expected_doses or 0)
        counted = (int(line.counted_doses) if line.counted_doses is not None else None)
        variance = (counted - expected) if counted is not None else None
        if variance is not None and variance != 0:
            has_variance = True
        total_expected += expected
        if counted is not None:
            total_counted += counted
        if variance is not None:
            total_variance_abs += abs(variance)
        rows.append([
            dose_label,
            qg_lot,
            exp,
            str(expected),
            "—" if counted is None else str(counted),
            "—" if variance is None else (f"+{variance}" if variance > 0 else str(variance)),
            (line.notes or "")[:90],
        ])

    rows.append([
        "TOTAL", "", "", str(total_expected), str(total_counted),
        "" if not has_variance else f"abs {total_variance_abs}", "",
    ])

    tbl = Table(rows, colWidths=[1.4 * inch, 1.1 * inch, 0.85 * inch, 0.65 * inch,
                                  0.65 * inch, 0.8 * inch, 1.95 * inch],
                  repeatRows=1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), PLUM),
        ("TEXTCOLOR",  (0, 0), (-1, 0), HexColor("#FFFFFF")),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 8.5),
        ("ALIGN",      (3, 1), (5, -1), "RIGHT"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.2, RULE),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
        ("BACKGROUND",   (0, -1), (-1, -1), PLUM_LIGHT),
        ("FONTNAME",     (0, -1), (-1, -1), "Helvetica-Bold"),
    ]
    # Color variance cells (data rows are indices 1..len(lines))
    for idx, line in enumerate(lines, start=1):
        if line.counted_doses is None:
            continue
        v = int(line.counted_doses) - int(line.expected_doses or 0)
        if v != 0:
            style_cmds.append(("TEXTCOLOR", (5, idx), (5, idx),
                                RED if v < 0 else AMBER))
            style_cmds.append(("FONTNAME", (5, idx), (5, idx), "Helvetica-Bold"))
    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)

    if not lines:
        story.append(Spacer(1, 6))
        story.append(Paragraph("No lots were in scope for this count.", muted))

    # ── Compliance + sign-off block ──
    story.append(Spacer(1, 14))
    story.append(Paragraph("Compliance attestation", section))
    story.append(Paragraph(
        "By signing below, the listed personnel certify that the doses recorded "
        "above were physically counted at the location and on the date stated, in "
        "accordance with 21 CFR 1304 perpetual-inventory requirements for "
        "Schedule III controlled substances.",
        body,
    ))
    story.append(Spacer(1, 14))

    signoff_rows = [
        ["Counter (printed name)", "", "Date", ""],
        ["Counter signature", "", "", ""],
        ["", "", "", ""],
        ["Witness (printed name)", "", "Date", ""],
        ["Witness signature", "", "", ""],
    ]
    signoff = Table(signoff_rows, colWidths=[1.65 * inch, 3.0 * inch,
                                              0.8 * inch, 1.85 * inch])
    signoff.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (1, 0), (1, 0), 0.6, INK),
        ("LINEBELOW", (1, 1), (1, 1), 0.6, INK),
        ("LINEBELOW", (1, 3), (1, 3), 0.6, INK),
        ("LINEBELOW", (1, 4), (1, 4), 0.6, INK),
        ("LINEBELOW", (3, 0), (3, 0), 0.6, INK),
        ("LINEBELOW", (3, 3), (3, 3), 0.6, INK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
    ]))
    story.append(KeepTogether([signoff]))

    story.append(Spacer(1, 10))
    story.append(footer_line(f"count_id {count.id}", styles))

    doc.build(story)
    size_bytes = os.path.getsize(out_path)
    return out_path, fname, size_bytes
