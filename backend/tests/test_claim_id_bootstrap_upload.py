"""Tests for POST /api/imports/claim-id-bootstrap (upload/preview)."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ClaimStatus, InsuranceOrder
from app.models.patient import Patient
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "claim_analysis_2026_01.xls"


def _upload(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/imports/claim-id-bootstrap",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
        )


def test_upload_returns_preview(client, db):
    r = _upload(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_rows"] == 1262
    assert body["unique_claims"] == 937
    # No seeded patients/claims → everything is "no_patient"
    assert body["no_patient"] == 937
    assert body["will_patch"] == 0
    assert "session_id" in body
    assert "expires_at" in body


def test_upload_bad_file_422(client, db):
    r = client.post(
        "/api/imports/claim-id-bootstrap",
        files={"file": ("x.txt", b"not excel", "text/plain")},
    )
    assert r.status_code == 422


def test_upload_forbidden_for_clinical(clinical_client, db):
    r = _upload(clinical_client)
    assert r.status_code == 403
