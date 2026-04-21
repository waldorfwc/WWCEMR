"""Tests for the Charge Analysis parser (pure function, no DB)."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import pandas as pd
import pytest

from app.services.charge_analysis_importer import (
    parse, ChargeAnalysisImport, ParsedClaim, ParsedServiceLine, ParseIssue,
)

FIXTURE = Path(__file__).parent / "fixtures" / "charge_analysis_test4.xls"


def _build_df(rows):
    """Build a minimal DataFrame with every column the parser requires."""
    columns = [
        "Patient: Patient ID", "Patient: First Name", "Patient: Last Name",
        "Date: Service date of the Charge", "Procedure: Code",
        "Provider: Rendering", "Location: Service Location", "Visit: Visit Type",
        "Adjustment: Net Non-Primary Ins. Adjusted",
        "Adjustment: Net Patient/Other Adjusted",
        "Adjustment: Net Primary Ins. Adjusted",
        "Charge Balance: Collection", "Charge Balance: Insurance",
        "Charge Balance: Patient", "Charge Balance: Total",
        "Charge: Charge Amount", "Diagnosis: Primary Code",
        "Diagnosis: Primary ICD-10 Code",
        "Insurance: Charge Primary Ins. Class",
        "Insurance: Charge Primary Ins. Company",
        "Insurance: Charge Primary Ins. Plan",
        "Insurance: Charge Primary Policy Number",
        "Insurance: Charge Secondary Ins. Class",
        "Insurance: Charge Secondary Ins. Company",
        "Insurance: Charge Secondary Ins. Plan",
        "Insurance: Charge Secondary Policy Number",
        "Patient: Date Of Birth", "Patient: Phone Primary",
        "Visit: VisitID", "Charge: Co-Pay", "Charge: Net Units",
        "Patient: Address Line 1", "Patient: Address Line 2",
        "Patient: City", "Patient: State", "Patient: Zip Code",
        "Payment: Net Patient/Other Applied",
        "Payment: Net Primary Ins. Applied",
        "Procedure: Modifiers", "Provider: Rendering NPI",
        "Charge: Charge Voids", "Charge: Void Indicator", "Patient: Sex",
        "Provider: Billable NPI", "Charge: Gross Charges",
    ]
    # Pad every row dict with None for columns not set
    filled = []
    for r in rows:
        d = {c: None for c in columns}
        d.update(r)
        filled.append(d)
    return pd.DataFrame(filled, columns=columns)


BASE_ROW = {
    "Patient: Patient ID": "11175",
    "Patient: First Name": "SILVINA",
    "Patient: Last Name": "DELFIN-CRUZ",
    "Date: Service date of the Charge": "1/2/2026",
    "Procedure: Code": 76830,
    "Provider: Rendering": "Cooke, Aryian MD",
    "Adjustment: Net Non-Primary Ins. Adjusted": 0,
    "Adjustment: Net Patient/Other Adjusted": 0,
    "Adjustment: Net Primary Ins. Adjusted": -169.95,
    "Charge Balance: Collection": 0,
    "Charge Balance: Insurance": 0,
    "Charge Balance: Patient": 0,
    "Charge Balance: Total": 0,
    "Charge: Charge Amount": 289.70,
    "Diagnosis: Primary ICD-10 Code": "R10.20",
    "Insurance: Charge Primary Ins. Company": "BCBS -Carefirst FEP/DC Local- SB580",
    "Insurance: Charge Primary Policy Number": "F5E816281807",
    "Insurance: Charge Secondary Ins. Company": "No Secondary Insurance Company",
    "Patient: Date Of Birth": "9/12/1979",
    "Patient: Phone Primary": "240-416-4826",
    "Visit: VisitID": 262924,
    "Charge: Co-Pay": 0,
    "Charge: Net Units": 1,
    "Patient: Address Line 1": "12566 COUNCIL OAK DR",
    "Patient: City": "Waldorf",
    "Patient: State": "MD",
    "Patient: Zip Code": 20601,
    "Payment: Net Patient/Other Applied": 0,
    "Payment: Net Primary Ins. Applied": -119.75,
    "Provider: Rendering NPI": 1124225222,
    "Charge: Charge Voids": 0,
    "Charge: Void Indicator": "NO",
    "Patient: Sex": "Female",
    "Provider: Billable NPI": 1124225222,
    "Charge: Gross Charges": 289.70,
}


def test_parse_returns_dataclass(tmp_path):
    df = _build_df([BASE_ROW])
    path = tmp_path / "one_row.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert isinstance(result, ChargeAnalysisImport)
    assert result.total_rows == 1
    assert result.skipped_voids == 0
    assert len(result.claims) == 1
    assert len(result.issues) == 0
    assert result.source_filename == "one_row.xlsx"


def test_parse_single_line_claim_maps_all_fields(tmp_path):
    df = _build_df([BASE_ROW])
    path = tmp_path / "one.xlsx"
    df.to_excel(path, index=False)
    c = parse(str(path)).claims[0]

    assert c.visit_id == "262924"
    assert c.patient_external_id == "11175"
    assert c.date_of_service_from == date(2026, 1, 2)
    assert c.payer_name == "BCBS -Carefirst FEP/DC Local- SB580"
    assert c.subscriber_id == "F5E816281807"
    assert c.rendering_provider_name == "Cooke, Aryian MD"
    assert c.rendering_provider_npi == "1124225222"
    assert c.billing_provider_npi == "1124225222"
    # Rollups from a single service line
    assert c.billed_amount == Decimal("289.70")
    assert c.paid_amount == Decimal("119.75")          # abs(-119.75)
    assert c.contractual_adjustment == Decimal("169.95")  # abs(-169.95)
    assert c.other_adjustment == Decimal("0")
    assert c.patient_responsibility == Decimal("0")

    assert len(c.service_lines) == 1
    sl = c.service_lines[0]
    assert sl.procedure_code == "76830"
    assert sl.units == Decimal("1")
    assert sl.billed_amount == Decimal("289.70")
    assert sl.paid_amount == Decimal("119.75")
    assert sl.contractual_adjustment == Decimal("169.95")
    assert sl.date_of_service_from == date(2026, 1, 2)
    assert sl.diagnosis_codes == ["R10.20"]


def test_parse_missing_required_column_raises(tmp_path):
    df = _build_df([BASE_ROW]).drop(columns=["Visit: VisitID"])
    path = tmp_path / "bad.xlsx"
    df.to_excel(path, index=False)
    with pytest.raises(ValueError) as exc:
        parse(str(path))
    assert "Visit: VisitID" in str(exc.value)


def test_parse_real_fixture_file():
    """Full-size fixture parse — 758 non-voided non-F.Chg claims."""
    result = parse(str(FIXTURE))
    assert result.total_rows == 1717
    assert result.skipped_voids == 104
    assert result.skipped_non_clinical == 602   # F.Chg finance charges
    assert len(result.claims) == 758
    assert all(len(c.service_lines) >= 1 for c in result.claims)
    errors = [i for i in result.issues if i.severity == "error"]
    assert errors == [], f"unexpected errors: {errors[:5]}"
