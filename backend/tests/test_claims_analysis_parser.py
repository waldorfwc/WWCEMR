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
    # Include any extra columns used in test rows (e.g. Follow-Up Date)
    extra = []
    for r in rows:
        for k in r:
            if k not in cols and k not in extra:
                extra.append(k)
    all_cols = cols + extra
    filled = []
    for r in rows:
        d = {c: None for c in all_cols}
        d.update(r)
        filled.append(d)
    return pd.DataFrame(filled, columns=all_cols)


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


# ============================ Phase 2d tests ============================
from app.models.claim import ClaimStatus
from app.services.claims_analysis_matcher import map_claim_status


def test_parse_reads_claim_status_and_state(tmp_path):
    row = {**BASE, "Claim Status": "Paid In Full", "Claim State": "Closed"}
    df = _build_df([row])
    path = tmp_path / "p.xlsx"
    df.to_excel(path, index=False)
    g = parse(str(path)).groups[0]
    assert g.claim_status_raw == "Paid In Full"
    assert g.claim_state == "Closed"


def test_parse_reads_follow_up_date(tmp_path):
    row = {**BASE, "Follow-Up Date": "2/15/2026"}
    df = _build_df([row])
    path = tmp_path / "p.xlsx"
    df.to_excel(path, index=False)
    g = parse(str(path)).groups[0]
    assert g.follow_up_date == date(2026, 2, 15)


def test_parse_reads_follow_up_reason_preserves_string(tmp_path):
    row = {**BASE, "Follow-Up Reason": "2-Claim Sent <15D"}
    df = _build_df([row])
    path = tmp_path / "p.xlsx"
    df.to_excel(path, index=False)
    g = parse(str(path)).groups[0]
    assert g.follow_up_reason == "2-Claim Sent <15D"


def test_parse_reads_last_submission_date(tmp_path):
    row = {**BASE, "Last Submission Date": "1/16/2026"}
    df = _build_df([row])
    path = tmp_path / "p.xlsx"
    df.to_excel(path, index=False)
    g = parse(str(path)).groups[0]
    assert g.last_submission_date == date(2026, 1, 16)


def test_parse_real_fixture_status_distribution():
    """Real fixture status distribution after mapping (unique Claim IDs, first-row-wins)."""
    result = parse(str(FIXTURE))
    mapped = [map_claim_status(g.claim_status_raw) for g in result.groups]
    from collections import Counter
    counts = Counter(mapped)
    assert counts[ClaimStatus.PAID] == 320
    assert counts[ClaimStatus.PARTIAL] == 14
    assert counts[ClaimStatus.PENDING] == 603


def test_status_mapping_known_values():
    assert map_claim_status("Paid In Full") == ClaimStatus.PAID
    assert map_claim_status("paid in full") == ClaimStatus.PAID   # case-insensitive
    assert map_claim_status("  Paid Partial  ") == ClaimStatus.PARTIAL  # whitespace-tolerant
    assert map_claim_status("New/No EOB") == ClaimStatus.PENDING


def test_status_mapping_unknown_returns_none():
    assert map_claim_status("Weird Value") is None
    assert map_claim_status("") is None
    assert map_claim_status(None) is None


def test_parse_warns_on_unknown_status(tmp_path):
    row = {**BASE, "Claim Status": "Weird Value"}
    df = _build_df([row])
    path = tmp_path / "p.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    warn = [i for i in result.issues if i.severity == "warning" and "claim status" in i.message.lower()]
    assert len(warn) == 1
