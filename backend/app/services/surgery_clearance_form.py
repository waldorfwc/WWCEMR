"""Generate a Cardiac/Anesthesia Clearance form PDF for a surgery.

The form is generated from scratch with reportlab (no template needed).
It includes patient identifiers, surgery details, the requesting practice's
contact info, and blanks for the cardiologist to fill in. The PDF is saved
as a SurgeryFile (kind='clearance_form') via the storage backend.

The intended workflow:
  1. Coordinator clicks "Generate Clearance Form" on the staff Pre-Surgery
     Coordination card.
  2. PDF is generated + emailed to the patient with portal upload link.
  3. Patient takes the form to their cardiologist, has it completed, then
     uploads the signed letter on the patient portal.
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime
from app.utils.dt import now_utc_naive
from typing import Optional

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgeryFile
from app.services.storage import save_blob

log = logging.getLogger(__name__)


def _age(dob: Optional[date]) -> Optional[int]:
    if not dob:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _primary_proc(s: Surgery) -> str:
    procs = s.procedures or []
    if not procs:
        return ""
    return procs[0].get("description") or ""


def _build_pdf(s: Surgery) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    # ─── Header ──────────────────────────────────────
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width / 2, height - 0.7 * inch,
                        "Cardiac / Anesthesia Pre-Operative Clearance")

    c.setFont("Helvetica", 9)
    c.drawCentredString(width / 2, height - 1.0 * inch,
                        "Waldorf Women's Care · Gynecology & Aesthetics")
    c.drawCentredString(width / 2, height - 1.15 * inch,
                        "Phone: 240-252-2140  ·  Fax: 240-252-2141")

    # Horizontal rule
    c.setLineWidth(0.8)
    c.line(0.6 * inch, height - 1.30 * inch,
           width - 0.6 * inch, height - 1.30 * inch)

    y = height - 1.6 * inch

    # ─── Patient block ───────────────────────────────
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.6 * inch, y, "Patient Information")
    y -= 0.22 * inch
    c.setFont("Helvetica", 10)

    def field(label: str, value: str):
        nonlocal y
        c.drawString(0.6 * inch, y, label)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(2.3 * inch, y, value or "—")
        c.setFont("Helvetica", 10)
        y -= 0.20 * inch

    field("Patient name:",      s.patient_name or "")
    field("Date of birth:",     f"{s.dob}  (age {_age(s.dob)})" if s.dob else "")
    field("Chart #:",            s.chart_number or "")
    field("Phone:",              s.cell_phone or s.phone or "")
    field("Primary insurance:",  s.primary_insurance or "")

    y -= 0.15 * inch

    # ─── Surgery block ───────────────────────────────
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.6 * inch, y, "Planned Procedure")
    y -= 0.22 * inch
    c.setFont("Helvetica", 10)

    field("Procedure:",  _primary_proc(s))
    field("Surgery date:",
          str(s.scheduled_date) if s.scheduled_date else "TBD")
    field("Facility:",
          {"medstar": "MedStar Southern Maryland Hospital Center",
           "crmc":    "University of Maryland Charles Regional Medical Center",
           "office":  "WWC Office"}.get(s.selected_facility or "", "—"))
    field("Primary surgeon:", s.surgeon_primary or "")
    if s.surgeon_secondary:
        field("Co-surgeon:", s.surgeon_secondary)

    y -= 0.15 * inch

    # ─── Cardiologist block (to be filled) ───────────
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.6 * inch, y, "Cardiologist / Anesthesia Provider")
    y -= 0.22 * inch
    c.setFont("Helvetica", 10)

    # If we have it already, prefill; else blank line for handwrite.
    field("Provider name:",  s.cardiologist_name or "")
    field("Phone:",          s.cardiologist_phone or "")
    field("Fax:",            s.cardiologist_fax or "")

    y -= 0.15 * inch

    # ─── Findings + signature block ──────────────────
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.6 * inch, y, "Clinical Findings")
    y -= 0.22 * inch
    c.setFont("Helvetica", 10)
    c.drawString(0.6 * inch, y,
                 "Please document pre-operative evaluation, EKG findings, "
                 "and clearance status below.")
    y -= 0.4 * inch

    # Lined area for findings (six lines)
    for i in range(6):
        c.line(0.6 * inch, y, width - 0.6 * inch, y)
        y -= 0.25 * inch

    y -= 0.1 * inch
    c.setFont("Helvetica-Bold", 10)
    c.drawString(0.6 * inch, y, "Clearance for surgery:")
    c.setFont("Helvetica", 10)
    c.drawString(2.5 * inch, y, "☐  CLEARED          ☐  CLEARED WITH CONDITIONS          ☐  NOT CLEARED")
    y -= 0.4 * inch

    c.line(0.6 * inch, y, 4.5 * inch, y)
    c.line(4.7 * inch, y, width - 0.6 * inch, y)
    c.setFont("Helvetica", 8)
    c.drawString(0.6 * inch, y - 0.12 * inch, "Provider signature")
    c.drawString(4.7 * inch, y - 0.12 * inch, "Date")

    # Footer
    c.setFont("Helvetica-Oblique", 8)
    c.drawCentredString(width / 2, 0.6 * inch,
        "Please return this completed form to the patient or fax to "
        "Waldorf Women's Care at 240-252-2141.")
    c.drawCentredString(width / 2, 0.45 * inch,
        f"Generated {now_utc_naive().strftime('%Y-%m-%d %H:%M UTC')}")

    c.showPage()
    c.save()
    return buf.getvalue()


def generate_for_surgery(db: Session, s: Surgery,
                         *, by_email: str) -> SurgeryFile:
    """Generate the PDF, persist via storage adapter, and write a SurgeryFile
    row (kind='clearance_form'). Returns the row."""
    pdf_bytes = _build_pdf(s)
    safe_chart = (s.chart_number or "unknown").replace("/", "_")
    filename = (
        f"clearance_form_{safe_chart}_"
        f"{now_utc_naive().strftime('%Y%m%d-%H%M%S')}.pdf"
    )
    key = save_blob(prefix="surgery_clearance_forms",
                    body=pdf_bytes, filename=filename)

    f = SurgeryFile(
        surgery_id=s.id,
        kind="clearance_form",
        filename=filename,
        path=key,
        mime_type="application/pdf",
        size_bytes=len(pdf_bytes),
        notes="Generated clearance form for cardiologist",
        uploaded_by=by_email,
    )
    db.add(f)
    db.commit(); db.refresh(f)
    return f
