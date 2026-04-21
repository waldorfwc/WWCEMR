"""Tests for the claim-id-bootstrap commit endpoint."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ServiceLine, ClaimStatus, InsuranceOrder
from app.models.patient import Patient
from app.models.audit import AuditLog
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "claim_analysis_2026_01.xls"


def _upload(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/imports/claim-id-bootstrap",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
        ).json()


def test_commit_patches_matching_primary_claim(client, db):
    # Seed: one patient + one claim that should match Claim ID 241786
    # (from the first row of real fixture).
    p = Patient(patient_id="11175", first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    # Fixture row 0 has Claim Amount 254.32 / DOS 1/2/2026 / Patient 11175
    c = Claim(
        claim_number="V1", patient_id=p.id,
        date_of_service_from=date(2026, 1, 2),
        billed_amount=Decimal("544.02"),
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("0"),
    )
    db.add(c); db.commit(); db.refresh(c)

    preview = _upload(client)
    assert preview["will_patch"] == 1

    r = client.post(f"/api/imports/claim-id-bootstrap/{preview['session_id']}/commit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["claims_patched"] == 1
    assert body["secondary_claims_created"] == 0

    db.refresh(c)
    assert c.patient_control_number == "241786P11175"


def test_commit_creates_secondary_claim_with_service_lines(client, db):
    # Build a synthetic scenario: patient + primary claim w/ 1 service line.
    # We craft a session directly to avoid depending on a secondary row
    # being present in the real fixture's first 20 rows.
    from app.services.claims_analysis_matcher import (
        ClaimsAnalysisGroup, MatchResult, ClaimsAnalysisImport,
    )
    from datetime import datetime, timezone, timedelta

    p = Patient(patient_id="99999", first_name="S", last_name="T")
    db.add(p); db.commit(); db.refresh(p)
    primary = Claim(
        claim_number="VSEC", patient_id=p.id,
        date_of_service_from=date(2026, 3, 1),
        billed_amount=Decimal("500.00"),
        payer_name="Primary BCBS",
        rendering_provider_name="Dr X", rendering_provider_npi="1111111111",
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("0"),
    )
    db.add(primary); db.commit(); db.refresh(primary)
    db.add(ServiceLine(claim_id=primary.id, procedure_code="99213",
                       units=Decimal("1"), billed_amount=Decimal("500.00")))
    db.commit()

    # Inject a secondary group that matches
    group = ClaimsAnalysisGroup(
        patient_external_id="99999", claim_id="888888",
        dos=date(2026, 3, 1), total_amount=Decimal("500.00"),
        row_count=1, insurance_priority="secondary",
        internal_claim_id="888888P99999",
    )
    match = MatchResult(group=group, status="will_create_secondary",
                        matched_claim_id=str(primary.id))
    parsed = ClaimsAnalysisImport(
        groups=[group], source_filename="synthetic.xls",
        total_rows=1, skipped_rows=0,
    )
    now = datetime.now(timezone.utc)
    import_sessions._sessions.clear()
    import_sessions._sessions["syn"] = import_sessions.SessionEntry(
        session_id="syn", payload={"parsed": parsed, "results": [match]},
        filename="synthetic.xls", file_path="/tmp/synthetic.xls",
        user_email="tester@waldorfwomenscare.com",
        created_at=now, expires_at=now + timedelta(minutes=30),
    )

    r = client.post("/api/imports/claim-id-bootstrap/syn/commit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["secondary_claims_created"] == 1

    secondary = db.query(Claim).filter(
        Claim.patient_id == p.id,
        Claim.insurance_order == InsuranceOrder.SECONDARY,
    ).first()
    assert secondary is not None
    assert secondary.patient_control_number == "888888P99999"
    assert secondary.billed_amount == Decimal("500.00")
    assert secondary.rendering_provider_npi == "1111111111"
    lines = db.query(ServiceLine).filter(ServiceLine.claim_id == secondary.id).all()
    assert len(lines) == 1
    assert lines[0].procedure_code == "99213"
    assert lines[0].paid_amount == 0  # Secondary starts at zero


def test_commit_404_on_unknown_session(client, db):
    r = client.post("/api/imports/claim-id-bootstrap/nope/commit")
    assert r.status_code == 404


def test_commit_forbidden_for_clinical(clinical_client, db):
    # Inject a synthetic session directly so we don't need the admin upload
    from datetime import datetime, timezone, timedelta
    from app.services.claims_analysis_matcher import ClaimsAnalysisImport
    now = datetime.now(timezone.utc)
    import_sessions._sessions.clear()
    import_sessions._sessions["syn"] = import_sessions.SessionEntry(
        session_id="syn",
        payload={"parsed": ClaimsAnalysisImport(groups=[], source_filename="x.xls",
                                                total_rows=0, skipped_rows=0),
                 "results": []},
        filename="x.xls", file_path="/tmp/x.xls",
        user_email="tester@waldorfwomenscare.com",
        created_at=now, expires_at=now + timedelta(minutes=30),
    )
    r = clinical_client.post("/api/imports/claim-id-bootstrap/syn/commit")
    assert r.status_code == 403
