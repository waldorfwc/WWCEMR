"""Tests for POST /api/imports/charge-analysis (upload → preview)."""
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ClaimStatus
from app.models.patient import Patient
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "charge_analysis_test4.xls"


def _upload(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/imports/charge-analysis",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
        )


def test_upload_returns_preview(client, db):
    r = _upload(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_filename"] == "charge_analysis_test4.xls"
    assert body["total_rows"] == 1717
    assert body["skipped_voids"] == 104
    assert body["skipped_non_clinical"] == 602
    assert body["parsed_claims"] == 758
    assert body["will_create"] == 758
    assert body["will_skip_existing"] == 0
    assert body["will_create_patients"] + body["will_match_patients"] == 758
    assert body["errors"] == 0
    assert len(body["sample_claims"]) == 20
    assert "expires_at" in body
    assert "session_id" in body


def test_upload_detects_existing_claim_by_visit_id(client, db):
    db.add(Claim(claim_number="263259", status=ClaimStatus.PENDING, balance=Decimal("0")))
    db.commit()
    r = _upload(client)
    assert r.status_code == 200
    body = r.json()
    assert body["will_skip_existing"] == 1
    assert body["will_create"] == 757


def test_upload_detects_matching_patient(client, db):
    db.add(Patient(patient_id="11175", first_name="Silvina", last_name="Delfin-Cruz"))
    db.commit()
    r = _upload(client)
    assert r.status_code == 200
    body = r.json()
    assert body["will_match_patients"] >= 1


def test_upload_bad_file_422(client, db):
    r = client.post(
        "/api/imports/charge-analysis",
        files={"file": ("bogus.txt", b"not an excel file", "text/plain")},
    )
    assert r.status_code == 422


def test_upload_missing_column_422(client, db, tmp_path):
    import pandas as pd
    # Export a DataFrame missing the VisitID column
    df = pd.DataFrame([{"Patient: Patient ID": "1"}])
    path = tmp_path / "bad.xlsx"
    df.to_excel(path, index=False)
    with path.open("rb") as f:
        r = client.post(
            "/api/imports/charge-analysis",
            files={"file": (path.name, f, "application/vnd.ms-excel")},
        )
    assert r.status_code == 422
    assert "missing required columns" in r.json()["detail"].lower() or "missing" in r.json()["detail"].lower()


def test_upload_forbidden_for_clinical(clinical_client, db):
    r = _upload(clinical_client)
    assert r.status_code == 403
