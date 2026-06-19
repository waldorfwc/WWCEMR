"""Boarding-slip template paths must resolve to the shipped PDFs.

Regression: the services-package reorg (daee081) moved boarding_slip.py one
level deeper but left the asset path at parents[1], so it pointed at the
non-existent app/services/assets/ and every boarding slip 500'd with
"template missing". This guards the resolved paths so a future move can't
silently break generation again.
"""
import io
import os
from datetime import date, time

from pypdf import PdfReader

from app.models.surgery import Surgery
from app.services.surgery import boarding_slip as bsl
from app.services.surgery.boarding_slip import CRMC_TEMPLATE, MEDSTAR_TEMPLATE


def test_medstar_template_exists():
    assert os.path.exists(MEDSTAR_TEMPLATE), MEDSTAR_TEMPLATE


def test_crmc_template_exists():
    assert os.path.exists(CRMC_TEMPLATE), CRMC_TEMPLATE


def _pdf_text(pdf_bytes):
    return "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def test_medstar_overlays_member_id_and_group(db):
    # The MedStar PDF has no ID/group field — they're overlaid below the
    # insurance-name boxes. Confirm they render onto the slip.
    s = Surgery(chart_number="1", patient_name="Doe, Jane", status="confirmed",
                selected_facility="medstar",
                primary_insurance="BCBS Federal", primary_member_id="XEG123456789",
                primary_group="170000", secondary_member_id="1EG4TE5MK72",
                procedures=[{"cpt": "58571", "description": "Lap hyst"}])
    text = _pdf_text(bsl.generate_medstar(s))
    assert "XEG123456789" in text       # primary member id
    assert "170000" in text             # primary group
    assert "1EG4TE5MK72" in text        # secondary member id


def test_medstar_no_insurance_overlay_when_blank(db):
    # No member id/group → no overlay, and still a valid PDF.
    s = Surgery(chart_number="2", patient_name="Roe, Pat", status="confirmed",
                selected_facility="medstar",
                procedures=[{"cpt": "58571", "description": "Lap hyst"}])
    pdf = bsl.generate_medstar(s)
    assert pdf[:5] == b"%PDF-"
