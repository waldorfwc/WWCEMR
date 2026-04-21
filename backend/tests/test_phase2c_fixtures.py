"""Smoke tests confirming Phase 2c fixtures load."""
from pathlib import Path
import pandas as pd
from app.parsers.era_835 import Era835Parser

FIXTURES = Path(__file__).parent / "fixtures"
CLAIMS_ANALYSIS = FIXTURES / "claim_analysis_2026_01.xls"
ERA_FILE = FIXTURES / "johns_hopkins_era.835"


def test_claims_analysis_fixture_shape():
    df = pd.read_excel(CLAIMS_ANALYSIS, sheet_name=0)
    assert df.shape == (1262, 49)
    for col in ("Patient ID", "Claim ID", "Date of Service",
                "Claim Amount", "Insurance Priority"):
        assert col in df.columns
    priorities = set(df["Insurance Priority"].dropna().unique())
    assert priorities == {"Primary", "Secondary"}
    assert df["Claim ID"].nunique() == 937


def test_era_fixture_parses():
    content = ERA_FILE.read_text()
    era = Era835Parser().parse(content, filename=ERA_FILE.name)
    assert era.payer_name == "JOHNS HOPKINS HEALTH PLANS"
    assert era.check_number == "355174145"
    assert len(era.claims) == 18
    assert era.parse_errors == []
    first = era.claims[0]
    assert first.patient_control_number == "216059P45740"
    assert first.claim_status_code == "1"
