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


def _fields(pdf_bytes):
    return PdfReader(io.BytesIO(pdf_bytes)).get_fields() or {}


def test_medstar_member_id_appends_next_to_insurance_name(db):
    # The MedStar PDF has no ID/group field — they're appended onto the
    # insurance-name field so they print next to the company name.
    s = Surgery(chart_number="1", patient_name="Doe, Jane", status="confirmed",
                selected_facility="medstar",
                primary_insurance="BCBS Federal", primary_member_id="XEG123456789",
                primary_group="170000", secondary_insurance="Medicare",
                secondary_member_id="1EG4TE5MK72",
                procedures=[{"cpt": "58571", "description": "Lap hyst"}])
    f = _fields(bsl.generate_medstar(s))
    name = f.get("AUTO_InsuranceName", {}).get("/V") or ""
    assert "BCBS Federal" in name and "XEG123456789" in name and "170000" in name
    sec = f.get("AUTO_SecondaryInsuranceName", {}).get("/V") or ""
    assert "Medicare" in sec and "1EG4TE5MK72" in sec


def test_medstar_override_member_id(db):
    s = Surgery(chart_number="3", patient_name="Roe, Pat", status="confirmed",
                selected_facility="medstar", primary_insurance="BCBS",
                procedures=[{"cpt": "58571", "description": "Lap hyst"}])
    f = _fields(bsl.generate_medstar(
        s, overrides={"AUTO_InsuranceName": "Aetna", "AUTO_InsuranceID": "OVR-999"}))
    name = f.get("AUTO_InsuranceName", {}).get("/V") or ""
    assert "Aetna" in name and "OVR-999" in name


def test_medstar_no_insurance_id_when_blank(db):
    # No member id/group → insurance name unchanged, still a valid PDF.
    s = Surgery(chart_number="2", patient_name="Roe, Pat", status="confirmed",
                selected_facility="medstar", primary_insurance="BCBS",
                procedures=[{"cpt": "58571", "description": "Lap hyst"}])
    pdf = bsl.generate_medstar(s)
    assert pdf[:5] == b"%PDF-"
    assert (_fields(pdf).get("AUTO_InsuranceName", {}).get("/V") or "") == "BCBS"
