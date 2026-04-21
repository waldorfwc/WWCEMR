"""Smoke test confirming the fixture file exists and loads as Excel."""
from pathlib import Path
import pandas as pd

FIXTURE = Path(__file__).parent / "fixtures" / "charge_analysis_test4.xls"


def test_fixture_exists():
    assert FIXTURE.exists(), f"missing fixture: {FIXTURE}"


def test_fixture_loads_with_pandas():
    df = pd.read_excel(FIXTURE, sheet_name=0)
    assert df.shape == (1717, 45)
    # Required anchor columns present
    assert "Visit: VisitID" in df.columns
    assert "Patient: Patient ID" in df.columns
    assert "Charge: Gross Charges" in df.columns
    assert "Charge: Void Indicator" in df.columns
