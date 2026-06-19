"""Generate filled boarding slips for hospital surgeries.

  MedStar — fills the existing fillable PDF's form fields directly.
  CRMC    — overlays text onto the scanned template at fixed coordinates.

Output is saved as a SurgeryFile row (kind='boarding_slip') and the path
returned for the caller to download or fax.
"""
from __future__ import annotations

import io
import json
import logging
import os
from datetime import date, datetime
from app.utils.dt import now_utc_naive
from pathlib import Path
from typing import Optional

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgeryFile
from app.services.storage import save_blob

log = logging.getLogger(__name__)

# Templates ship inside the container image at backend/app/assets/.
# This file lives at app/services/surgery/boarding_slip.py, so app/ is
# parents[2] (surgery → services → app). The services-package reorg
# (daee081) moved this file one level deeper and left this at parents[1],
# which pointed at the non-existent app/services/assets/ — every boarding
# slip then 500'd with "template missing".
_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "boarding_slip_templates"
MEDSTAR_TEMPLATE = str(_ASSETS / "medstar_template.pdf")
CRMC_TEMPLATE    = str(_ASSETS / "crmc_template.pdf")


# ─── Helpers ──────────────────────────────────────────────────────

def _age(dob: Optional[date]) -> Optional[int]:
    if not dob:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _name_last_first(s: Surgery) -> tuple[str, str]:
    if s.last_name and s.first_name:
        return s.last_name, s.first_name
    parts = (s.patient_name or "").split(",", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return s.patient_name or "", ""


def _primary_proc(s: Surgery) -> tuple[str, str]:
    """(cpt, description) for the first procedure."""
    procs = s.procedures or []
    if not procs:
        return "", ""
    p = procs[0]
    return p.get("cpt") or "", p.get("description") or ""


def _hours_minutes(total_min: Optional[int]) -> tuple[str, str]:
    if not total_min:
        return "", ""
    h = total_min // 60
    m = total_min % 60
    return str(h), str(m)


def _us_date(d) -> str:
    """Format a date (or YYYY-MM-DD string) as MM/DD/YYYY for the printed
    form. Returns '' for None / blank input."""
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = datetime.strptime(d[:10], "%Y-%m-%d").date()
        except ValueError:
            return d  # leave already-formatted strings alone
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


# ─── MedStar (fillable PDF form) ──────────────────────────────────

def generate_medstar(s: Surgery, overrides: Optional[dict] = None) -> bytes:
    """Fill the MedStar Posting Form. Returns the new PDF bytes.

    `overrides` is an optional dict of {field_name: value} that wins over
    the surgery-derived defaults — used by the staff PDF editor."""
    if not os.path.exists(MEDSTAR_TEMPLATE):
        raise RuntimeError(f"MedStar template missing: {MEDSTAR_TEMPLATE}")

    last, first = _name_last_first(s)
    cpt, descr = _primary_proc(s)
    procs = s.procedures or []
    secondary_cpt = procs[1]["cpt"] if len(procs) > 1 and procs[1].get("cpt") else ""
    diags = s.diagnoses or []
    icd = diags[0]["icd"] if diags and diags[0].get("icd") else ""
    impression = diags[0]["description"] if diags and diags[0].get("description") else ""
    h, m = _hours_minutes(s.estimated_minutes)

    fields = {
        "Surgery Date Requested":     _us_date(s.scheduled_date),
        "Start Time":                 (str(s.scheduled_start_time)[:5] if s.scheduled_start_time else ""),
        "Secondary Surgeon":          s.surgeon_secondary or "",
        "Est Time Needed":            "",
        "Hrs":                        h,
        # "Min Primary CPT Code" is one quirky field on the source PDF — we
        # use it for the CPT (the Hrs/Min split is captured separately).
        "Min Primary CPT Code":       cpt,
        "Secondary CPT":              secondary_cpt,
        "AUTO_PatientNameLast":       last,
        "AUTO_PatientNameFirst":      first,
        "AUTO_PatientDateOfBirth":    _us_date(s.dob),
        "AUTO_PatientAge":            str(_age(s.dob)) if s.dob else "",
        "AUTO_CurrentDate":           _us_date(date.today()),
        "AUTO_PatientPhone":          s.cell_phone or s.phone or "",
        "AUTO_PatientAddress":        s.address_street or "",
        "AUTO_PatientCity":           s.address_city or "",
        "AUTO_PatientState":          s.address_state or "",
        "AUTO_PatientZip":            s.address_zip or "",
        "AUTO_PhysicianName":         s.surgeon_primary or "",
        "AUTO_InsuranceName":         s.primary_insurance or "",
        "AUTO_SecondaryInsuranceName": s.secondary_insurance or "",
        "AUTO_VisitICD10":            icd,
        "AUTO_VisitImpressions":      impression,
        "AUTO_VisitPlans":            descr,
        "Other Special Equipment":    (s.special_equipment_notes or "")[:200],
        "Vendor Company":             s.device_kind or "",
        "Reps Name":                  s.rep_name or "",
        "Additional Notes":           (s.notes or "")[:200],
    }

    # Apply caller overrides on top so staff can correct prefilled values.
    if overrides:
        for k, v in overrides.items():
            if v is None:
                continue
            fields[k] = str(v)

    reader = PdfReader(MEDSTAR_TEMPLATE)
    writer = PdfWriter(clone_from=reader)
    # Fill on every page that has fields
    for page in writer.pages:
        try:
            writer.update_page_form_field_values(page, fields)
        except Exception:
            # Pages without form widgets raise — skip
            pass

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ─── CRMC (overlay onto scanned template) ─────────────────────────

# Coordinate map for CRMC posting form. The scanned PDF is letter size.
# Coords are (x, y) from bottom-left in points; tweak after a test print.
CRMC_COORDS = {
    "requested_date":    (180, 645),     # Requested Date: __/__/__
    "requested_time":    (380, 645),
    "outpatient":        (495, 622),     # checkbox X
    "inpatient":         (430, 622),
    "procedure":         (140, 595),     # primary procedure description + CPT
    "diagnosis":         (140, 558),
    "anesthesia":        (440, 558),
    "icd":               (115, 535),
    "cpt":               (370, 535),
    "frozen_section":    (90, 510),
    "biopsy_yes":        (320, 510),
    "biopsy_no":         (350, 510),
    "special_request":   (140, 488),
    # Patient block
    "last_name":         (140, 425),
    "first_name":        (380, 425),
    "dob":               (140, 400),
    "ssn":               (380, 400),
    "address":           (140, 376),
    "city":              (380, 376),
    "zip":               (510, 376),
    "home_phone":        (140, 352),
    "cell_phone":        (380, 352),
    "work_phone":        (140, 328),
    "other_phone":       (380, 328),
    # Insurance
    "pcp":               (140, 295),
    "office_number":     (380, 295),
    "primary_ins":       (140, 270),
    "policy_holder_p":   (380, 270),
    "ins_id_p":          (140, 246),
    "group_p":           (380, 246),
    "secondary_ins":     (140, 222),
    "policy_holder_s":   (380, 222),
    "ins_id_s":          (140, 198),
    "group_s":           (380, 198),
    "auth_number":       (260, 174),
}


def generate_crmc(s: Surgery, overrides: Optional[dict] = None) -> bytes:
    """Overlay surgery info onto the CRMC scanned template. Returns the
    new PDF bytes.

    `overrides` is an optional dict of {coord_key: value} that wins over
    the surgery-derived defaults — used by the staff PDF editor."""
    if not os.path.exists(CRMC_TEMPLATE):
        raise RuntimeError(f"CRMC template missing: {CRMC_TEMPLATE}")

    last, first = _name_last_first(s)
    cpt, descr = _primary_proc(s)
    diag_text = ""
    icd = ""
    if s.diagnoses:
        d = s.diagnoses[0]
        diag_text = d.get("description") or ""
        icd = d.get("icd") or ""

    auth_text = s.auth_number or ("Not Required" if s.auth_status == "not_required" else "")

    # Build the data map first, then apply overrides, then draw.
    data = {
        "requested_date":    _us_date(s.scheduled_date),
        "requested_time":    str(s.scheduled_start_time)[:5] if s.scheduled_start_time else "",
        "outpatient":        "X",
        "procedure":         (descr or "")[:60],
        "diagnosis":         (diag_text or "")[:60],
        "anesthesia":        s.anesthesia or "",
        "icd":               icd,
        "cpt":               cpt,
        "special_request":   (s.special_equipment_notes or "")[:60],
        "last_name":         last,
        "first_name":        first,
        "dob":               _us_date(s.dob),
        "address":           s.address_street or "",
        "city":              s.address_city or "",
        "zip":               s.address_zip or "",
        "home_phone":        s.phone or "",
        "cell_phone":        s.cell_phone or "",
        "primary_ins":       s.primary_insurance or "",
        "policy_holder_p":   s.patient_name or "",
        "ins_id_p":          s.primary_member_id or "",
        "group_p":           s.primary_group or "",
        "secondary_ins":     s.secondary_insurance or "No Secondary",
        "ins_id_s":          s.secondary_member_id or "",
        "auth_number":       auth_text,
    }
    if overrides:
        for k, v in overrides.items():
            if v is None:
                continue
            data[k] = str(v)

    # Build the overlay PDF
    overlay = io.BytesIO()
    c = canvas.Canvas(overlay, pagesize=letter)
    c.setFont("Helvetica", 9)

    def write(key, val):
        x, y = CRMC_COORDS.get(key, (None, None))
        if x is None or not val:
            return
        c.drawString(x, y, str(val)[:60])

    for k, v in data.items():
        write(k, v)

    c.showPage()
    c.save()
    overlay.seek(0)

    # Merge overlay onto the scanned template's first page
    template = PdfReader(CRMC_TEMPLATE)
    overlay_pdf = PdfReader(overlay)
    writer = PdfWriter()
    page = template.pages[0]
    page.merge_page(overlay_pdf.pages[0])
    writer.add_page(page)
    # Append remaining pages (if any) untouched
    for p in template.pages[1:]:
        writer.add_page(p)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ─── Public API ──────────────────────────────────────────────────

def generate_for_surgery(db: Session, s: Surgery, *, by_email: str,
                          overrides: Optional[dict] = None) -> SurgeryFile:
    """Pick the right generator based on facility, persist via storage
    adapter, and write a SurgeryFile row pointing at the storage key.

    `overrides` is forwarded to the underlying generator so staff can
    correct prefilled fields before regenerating."""
    if s.selected_facility == "medstar":
        pdf_bytes = generate_medstar(s, overrides=overrides)
        slug = "medstar"
    elif s.selected_facility == "crmc":
        pdf_bytes = generate_crmc(s, overrides=overrides)
        slug = "crmc"
    else:
        raise ValueError(f"No boarding slip needed for facility={s.selected_facility}")

    safe_chart = (s.chart_number or "unknown").replace("/", "_")
    fname = (
        f"{slug}_{safe_chart}_"
        f"{now_utc_naive().strftime('%Y%m%d-%H%M%S')}.pdf"
    )
    key = save_blob(prefix="surgery_boarding_slips",
                    body=pdf_bytes, filename=fname)

    note_parts = [f"Generated for {s.selected_facility}"]
    if overrides:
        note_parts.append("overrides=" + json.dumps(overrides))

    f = SurgeryFile(
        surgery_id=s.id,
        kind="boarding_slip",
        filename=fname,
        path=key,
        mime_type="application/pdf",
        size_bytes=len(pdf_bytes),
        notes=" | ".join(note_parts),
        uploaded_by=by_email,
    )
    db.add(f)
    db.commit(); db.refresh(f)
    return f
