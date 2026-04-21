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


def test_parse_multi_line_claim_groups_and_rolls_up(tmp_path):
    row_a = {**BASE_ROW, "Visit: VisitID": 999, "Procedure: Code": "99213",
             "Charge: Gross Charges": 100.00, "Adjustment: Net Primary Ins. Adjusted": -30.00,
             "Payment: Net Primary Ins. Applied": -50.00, "Charge Balance: Patient": 20.00,
             "Diagnosis: Primary ICD-10 Code": "R10.20"}
    row_b = {**BASE_ROW, "Visit: VisitID": 999, "Procedure: Code": "76830",
             "Charge: Gross Charges": 200.00, "Adjustment: Net Primary Ins. Adjusted": -60.00,
             "Payment: Net Primary Ins. Applied": -140.00, "Charge Balance: Patient": 0,
             "Diagnosis: Primary ICD-10 Code": "N92.0"}
    df = _build_df([row_a, row_b])
    path = tmp_path / "two.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))

    assert len(result.claims) == 1
    c = result.claims[0]
    assert c.visit_id == "999"
    assert len(c.service_lines) == 2
    # Rollups are sums across lines
    assert c.billed_amount == Decimal("300.00")
    assert c.paid_amount == Decimal("190.00")          # 50 + 140
    assert c.contractual_adjustment == Decimal("90.00")  # 30 + 60
    assert c.patient_responsibility == Decimal("20.00")
    # Order preserved
    assert [sl.procedure_code for sl in c.service_lines] == ["99213", "76830"]
    assert [sl.diagnosis_codes for sl in c.service_lines] == [["R10.20"], ["N92.0"]]


def test_parse_payer_differs_across_lines_warns(tmp_path):
    row_a = {**BASE_ROW, "Visit: VisitID": 101, "Procedure: Code": "99213",
             "Insurance: Charge Primary Ins. Company": "Aetna"}
    row_b = {**BASE_ROW, "Visit: VisitID": 101, "Procedure: Code": "76830",
             "Insurance: Charge Primary Ins. Company": "BCBS"}
    df = _build_df([row_a, row_b])
    path = tmp_path / "diff.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))

    assert result.claims[0].payer_name == "Aetna"  # first wins
    warnings = [i for i in result.issues if i.severity == "warning" and "payer name" in i.message]
    assert len(warnings) == 1


def test_parse_secondary_placeholder_treated_as_none(tmp_path):
    row = {**BASE_ROW, "Insurance: Charge Secondary Ins. Company": "No Secondary Insurance Company"}
    df = _build_df([row])
    path = tmp_path / "no_sec.xlsx"
    df.to_excel(path, index=False)
    c = parse(str(path)).claims[0]
    assert c.secondary_payer_name is None
    assert c.secondary_subscriber_id is None


def test_parse_voided_row_skipped(tmp_path):
    row_good = {**BASE_ROW, "Visit: VisitID": 1}
    row_void = {**BASE_ROW, "Visit: VisitID": 2, "Charge: Void Indicator": "YES"}
    df = _build_df([row_good, row_void])
    path = tmp_path / "voided.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert result.skipped_voids == 1
    assert len(result.claims) == 1
    assert result.claims[0].visit_id == "1"


def test_parse_multi_unit_service_line(tmp_path):
    row = {**BASE_ROW, "Procedure: Code": "J2003",
           "Charge: Net Units": 20, "Charge: Charge Amount": 1.50,
           "Charge: Gross Charges": 30.00,
           "Adjustment: Net Primary Ins. Adjusted": -29.80,
           "Payment: Net Primary Ins. Applied": -0.20}
    df = _build_df([row])
    path = tmp_path / "multi_unit.xlsx"
    df.to_excel(path, index=False)
    sl = parse(str(path)).claims[0].service_lines[0]
    assert sl.units == Decimal("20")
    assert sl.billed_amount == Decimal("30.00")
    assert sl.contractual_adjustment == Decimal("29.80")
    assert sl.paid_amount == Decimal("0.20")


def test_parse_payment_negative_sign_normalized(tmp_path):
    row = {**BASE_ROW, "Payment: Net Primary Ins. Applied": -119.75,
           "Adjustment: Net Primary Ins. Adjusted": -169.95,
           "Adjustment: Net Non-Primary Ins. Adjusted": -10.00}
    df = _build_df([row])
    path = tmp_path / "signs.xlsx"
    df.to_excel(path, index=False)
    c = parse(str(path)).claims[0]
    assert c.paid_amount == Decimal("119.75")
    assert c.contractual_adjustment == Decimal("169.95")
    assert c.other_adjustment == Decimal("10.00")


def test_parse_modifier_splitting_two(tmp_path):
    row = {**BASE_ROW, "Procedure: Modifiers": "25 59"}
    df = _build_df([row])
    path = tmp_path / "mods2.xlsx"
    df.to_excel(path, index=False)
    sl = parse(str(path)).claims[0].service_lines[0]
    assert sl.modifier_1 == "25"
    assert sl.modifier_2 == "59"
    assert sl.modifier_3 is None
    assert sl.modifier_4 is None


def test_parse_modifier_overflow_warns(tmp_path):
    row = {**BASE_ROW, "Procedure: Modifiers": "25 59 76 RT LT"}
    df = _build_df([row])
    path = tmp_path / "mods5.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    sl = result.claims[0].service_lines[0]
    assert (sl.modifier_1, sl.modifier_2, sl.modifier_3, sl.modifier_4) == ("25", "59", "76", "RT")
    warn = [i for i in result.issues if "more than 4 modifiers" in i.message]
    assert len(warn) == 1


def test_parse_missing_visit_id_row_dropped(tmp_path):
    row = {**BASE_ROW, "Visit: VisitID": None}
    df = _build_df([row])
    path = tmp_path / "no_visit.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert result.claims == []
    errs = [i for i in result.issues if i.severity == "error"]
    assert len(errs) == 1
    assert "Visit: VisitID" in errs[0].message


def test_parse_non_numeric_charge_dropped(tmp_path):
    row = {**BASE_ROW, "Charge: Gross Charges": "bogus"}
    df = _build_df([row])
    path = tmp_path / "bad_charge.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert result.claims == []
    errs = [i for i in result.issues if "non-numeric" in i.message]
    assert len(errs) == 1


def test_parse_negative_charge_warns_but_parses(tmp_path):
    row = {**BASE_ROW, "Charge: Gross Charges": -100.00}
    df = _build_df([row])
    path = tmp_path / "negative.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert len(result.claims) == 1
    warn = [i for i in result.issues if "negative" in i.message]
    assert len(warn) == 1


def test_parse_demographics_packs_address(tmp_path):
    df = _build_df([BASE_ROW])
    path = tmp_path / "demo.xlsx"
    df.to_excel(path, index=False)
    d = parse(str(path)).claims[0].patient_demographics
    assert d["first_name"] == "SILVINA"
    assert d["last_name"] == "DELFIN-CRUZ"
    assert d["date_of_birth"] == date(1979, 9, 12)
    assert d["phone"] == "240-416-4826"
    assert "12566 COUNCIL OAK DR" in d["address"]
    assert "Waldorf, MD 20601" in d["address"]
    assert d["_sex"] == "Female"  # captured; dropped on persist
