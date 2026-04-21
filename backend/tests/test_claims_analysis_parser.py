"""Parser tests for Claims Analysis (Part 1 of Phase 2c)."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import pandas as pd
import pytest
from app.services.claims_analysis_matcher import (
    parse, ClaimsAnalysisImport, ClaimsAnalysisGroup,
)

FIXTURE = Path(__file__).parent / "fixtures" / "claim_analysis_2026_01.xls"


def _build_df(rows):
    cols = [
        "Patient ID", "Patient Name", "Care Provider", "Insurance Class",
        "Claim Amount", "Claim ID", "Claim State", "Claim Status",
        "Date of Service", "Filing Method", "Insurance Company",
        "Insurance Priority", "Line Balance", "Payor ID",
    ]
    filled = []
    for r in rows:
        d = {c: None for c in cols}
        d.update(r)
        filled.append(d)
    return pd.DataFrame(filled, columns=cols)


BASE = {
    "Patient ID": "11175", "Patient Name": "DOE, JANE",
    "Claim ID": 241786, "Claim Amount": 254.32,
    "Date of Service": "1/2/2026", "Insurance Priority": "Primary",
    "Insurance Company": "BCBS", "Payor ID": "00580",
}


def test_parse_real_fixture():
    result = parse(str(FIXTURE))
    assert isinstance(result, ClaimsAnalysisImport)
    assert result.total_rows == 1262
    assert len(result.groups) == 937
    assert all(isinstance(g, ClaimsAnalysisGroup) for g in result.groups)
    # Matches the 911 primary + 26 secondary in real data
    primary = sum(1 for g in result.groups if g.insurance_priority == "primary")
    secondary = sum(1 for g in result.groups if g.insurance_priority == "secondary")
    assert primary == 911
    assert secondary == 26


def test_parse_missing_required_column_raises(tmp_path):
    df = _build_df([BASE]).drop(columns=["Claim ID"])
    path = tmp_path / "bad.xlsx"
    df.to_excel(path, index=False)
    with pytest.raises(ValueError) as exc:
        parse(str(path))
    assert "Claim ID" in str(exc.value)


def test_parse_drops_rows_with_null_patient_id(tmp_path):
    df = _build_df([BASE, {**BASE, "Patient ID": None}])
    path = tmp_path / "null_pid.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert len(result.groups) == 1
    assert result.skipped_rows == 1


def test_parse_groups_by_claim_id_and_sums(tmp_path):
    row_a = {**BASE, "Claim ID": 999, "Claim Amount": 100.00}
    row_b = {**BASE, "Claim ID": 999, "Claim Amount": 150.00}
    df = _build_df([row_a, row_b])
    path = tmp_path / "group.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert len(result.groups) == 1
    g = result.groups[0]
    assert g.claim_id == "999"
    assert g.total_amount == Decimal("250.00")
    assert g.row_count == 2
    assert g.internal_claim_id == "999P11175"


def test_parse_normalizes_priority_lowercase(tmp_path):
    df = _build_df([BASE])
    path = tmp_path / "p.xlsx"
    df.to_excel(path, index=False)
    g = parse(str(path)).groups[0]
    assert g.insurance_priority == "primary"


def test_parse_unknown_priority_warns_and_defaults(tmp_path):
    row = {**BASE, "Insurance Priority": "Weirdness"}
    df = _build_df([row])
    path = tmp_path / "u.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert result.groups[0].insurance_priority == "primary"
    warns = [i for i in result.issues if "unknown priority" in i.message.lower()]
    assert len(warns) == 1
