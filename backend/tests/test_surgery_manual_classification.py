"""create_manual honors an explicit procedure_classification when supplied,
and still derives it from CPTs when omitted. `client` is the super-admin fixture."""
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _no_pg_sequence():
    # next_surgery_number() relies on a Postgres sequence (nextval) that
    # SQLite test DBs don't have. Stub the assignment for these tests.
    with patch(
        "app.services.surgery.local_helpers.maybe_assign_surgery_number",
        return_value="SUR00001",
    ):
        yield


_BASE = dict(
    chart_number="MRN-CLS-1", patient_name="Test, Pat", dob="1980-01-01",
    phone="3015551212", email="pat@example.com",
    address_street="1 A St", address_city="Town", address_state="MD", address_zip="20601",
    primary_insurance="Aetna", primary_member_id="X1", surgeon_primary="Cooke, Aryian, MD",
    diagnoses=[{"icd": "N93.9", "description": "AUB"}],
    eligible_facilities=["office"], estimated_minutes=30, preop_date="2026-07-01",
)


def test_explicit_classification_wins(client):
    # Genuine conflict: 49320 is a MAJOR CPT at a non-office facility, so the
    # legacy derivation would produce "major". The explicit "office" must win.
    body = dict(_BASE, surgery_name="Diagnostic laparoscopy",
                eligible_facilities=["medstar"],
                procedures=[{"cpt": "49320", "description": "Diagnostic laparoscopy"}],
                procedure_classification="office")
    r = client.post("/api/surgery/manual", json=body)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    got = client.get(f"/api/surgery/{sid}").json()
    assert got["procedure_classification"] == "office"


def test_omitted_classification_is_derived(client):
    body = dict(_BASE, chart_number="MRN-CLS-2",
                surgery_name="Diagnostic laparoscopy",
                procedures=[{"cpt": "49320", "description": "Diagnostic laparoscopy"}])
    r = client.post("/api/surgery/manual", json=body)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    got = client.get(f"/api/surgery/{sid}").json()
    assert got["procedure_classification"] == "major"   # 49320 in MAJOR -> derived
