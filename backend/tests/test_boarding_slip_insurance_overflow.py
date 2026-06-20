"""MedStar insurance-name field must not overflow the fixed-width box.

`generate_medstar` appends `ID {member_id}  Grp {group}` onto the
AUTO_InsuranceName field. A long insurer + ID + group could overflow/clip on
the printed slip, so the composed value is capped to _INSURANCE_FIELD_MAX.
Truncation never splits the member ID.
"""
import io

from pypdf import PdfReader

from app.models.surgery import Surgery
from app.services.surgery import boarding_slip as bsl
from app.services.surgery.boarding_slip import _INSURANCE_FIELD_MAX


def _fields(pdf_bytes):
    return PdfReader(io.BytesIO(pdf_bytes)).get_fields() or {}


def test_medstar_primary_insurance_name_capped_member_id_intact(db):
    long_insurer = "Blue Cross Blue Shield Federal Employee Program PPO Nationwide"
    member_id = "XEG999888777666"
    s = Surgery(chart_number="OVF1", patient_name="Doe, Jane", status="confirmed",
                selected_facility="medstar",
                primary_insurance=long_insurer,
                primary_member_id=member_id,
                primary_group="GROUP-1700000000",
                procedures=[{"cpt": "58571", "description": "Lap hyst"}])
    f = _fields(bsl.generate_medstar(s))
    val = f.get("AUTO_InsuranceName", {}).get("/V") or ""
    assert len(val) <= _INSURANCE_FIELD_MAX, (len(val), val)
    # The member ID must be fully present, never split.
    assert member_id in val, val


def test_medstar_secondary_insurance_name_capped_member_id_intact(db):
    long_insurer = "United Healthcare Choice Plus National Network Group Plan Extended"
    member_id = "1EG4TE5MK72ABCDEF"
    s = Surgery(chart_number="OVF2", patient_name="Roe, Pat", status="confirmed",
                selected_facility="medstar",
                primary_insurance="BCBS",
                secondary_insurance=long_insurer,
                secondary_member_id=member_id,
                procedures=[{"cpt": "58571", "description": "Lap hyst"}])
    f = _fields(bsl.generate_medstar(s))
    val = f.get("AUTO_SecondaryInsuranceName", {}).get("/V") or ""
    assert len(val) <= _INSURANCE_FIELD_MAX, (len(val), val)
    assert member_id in val, val
