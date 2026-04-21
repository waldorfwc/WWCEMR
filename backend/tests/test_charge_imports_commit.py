"""Tests for POST /api/imports/charge-analysis/{session_id}/commit."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ServiceLine, ClaimStatus
from app.models.patient import Patient
from app.models.audit import AuditLog
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "charge_analysis_test4.xls"


def _upload(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        r = client.post(
            "/api/imports/charge-analysis",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
        )
    return r.json()


def test_commit_creates_claims_and_service_lines(client, db):
    preview = _upload(client)
    r = client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["claims_created"] == 758
    assert body["claims_skipped_existing"] == 0
    # 1717 total rows - 104 voided - 602 non-clinical (F.Chg) = 1011
    assert body["service_lines_created"] == 1011
    assert body["errors"] == []

    assert db.query(Claim).count() == 758
    assert db.query(ServiceLine).count() == 1011


def test_commit_skips_existing_claim_by_visit_id(client, db):
    db.add(Claim(claim_number="263259", status=ClaimStatus.PENDING, balance=Decimal("0")))
    db.commit()
    preview = _upload(client)
    r = client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    body = r.json()
    assert body["claims_created"] == 757
    assert body["claims_skipped_existing"] == 1
    # The pre-seeded claim is still a single row, untouched
    existing = db.query(Claim).filter(Claim.claim_number == "263259").all()
    assert len(existing) == 1
    assert db.query(ServiceLine).filter(ServiceLine.claim_id == existing[0].id).count() == 0


def test_commit_creates_missing_patient(client, db):
    # Pre-seed one patient; commit should match it and create all others new
    db.add(Patient(patient_id="11175", first_name="Silvina", last_name="Delfin-Cruz"))
    db.commit()
    preview = _upload(client)
    r = client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    body = r.json()
    assert body["patients_matched"] >= 1
    assert body["patients_created"] >= 1
    # The seeded patient wasn't duplicated
    assert db.query(Patient).filter(Patient.patient_id == "11175").count() == 1


def test_commit_does_not_duplicate_existing_patient(client, db):
    db.add(Patient(patient_id="11175", first_name="Silvina", last_name="Delfin-Cruz"))
    db.commit()
    preview = _upload(client)
    client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    assert db.query(Patient).filter(Patient.patient_id == "11175").count() == 1


def test_commit_recomputes_claim_balance(client, db):
    preview = _upload(client)
    client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    # Pick any claim from the set; its balance must equal
    # billed - contractual - other - paid - pt_resp.
    claim = db.query(Claim).first()
    expected = (claim.billed_amount or 0) - (claim.contractual_adjustment or 0) \
               - (claim.other_adjustment or 0) - (claim.paid_amount or 0) \
               - (claim.patient_responsibility or 0)
    assert float(claim.balance) == float(expected)


def test_commit_writes_audit_row_per_claim(client, db):
    preview = _upload(client)
    client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    create_rows = db.query(AuditLog).filter(
        AuditLog.action == "CREATE",
        AuditLog.resource_type == "claim",
    ).all()
    assert len(create_rows) == 758
    assert all(r.user_name == "tester@waldorfwomenscare.com" for r in create_rows)
    # Each claim is linked to a patient (either matched or newly created)
    assert all(r.patient_id is not None for r in create_rows)


def test_commit_writes_single_import_audit_row(client, db):
    preview = _upload(client)
    sid = preview["session_id"]
    client.post(f"/api/imports/charge-analysis/{sid}/commit")
    import_rows = db.query(AuditLog).filter(
        AuditLog.resource_type == "charge_analysis_file",
    ).all()
    assert len(import_rows) == 1
    assert import_rows[0].action == "IMPORT"
    assert import_rows[0].resource_id == sid


def test_commit_404_on_unknown_session(client, db):
    r = client.post("/api/imports/charge-analysis/not-a-session/commit")
    assert r.status_code == 404


def test_commit_404_on_expired_session(client, db):
    # Manufacture an expired session in the store directly
    past = datetime.now(timezone.utc) - timedelta(minutes=45)
    entry = import_sessions.SessionEntry(
        session_id="expired", payload=None, filename="f", file_path="/tmp/f",
        user_email="u@x", created_at=past, expires_at=past + timedelta(minutes=30),
        claim_flags=[],
    )
    import_sessions._sessions["expired"] = entry  # bypass put() TTL
    r = client.post("/api/imports/charge-analysis/expired/commit")
    assert r.status_code == 404


def test_commit_session_is_purged_after_success(client, db):
    preview = _upload(client)
    sid = preview["session_id"]
    client.post(f"/api/imports/charge-analysis/{sid}/commit")
    assert import_sessions.get(sid) is None


def test_commit_forbidden_for_clinical(clinical_client, db):
    # A session in the store is not user-bound; a clinical user attempting
    # to commit any session must still be 403'd by the billing-router guard.
    now = datetime.now(timezone.utc)
    import_sessions._sessions.clear()
    import_sessions._sessions["sess-x"] = import_sessions.SessionEntry(
        session_id="sess-x", payload=None, filename="f", file_path="/tmp/f",
        user_email="admin@x", created_at=now, expires_at=now + timedelta(minutes=30),
        claim_flags=[],
    )
    r = clinical_client.post("/api/imports/charge-analysis/sess-x/commit")
    assert r.status_code == 403


def test_upload_re_run_shows_all_existing(client, db):
    preview1 = _upload(client)
    client.post(f"/api/imports/charge-analysis/{preview1['session_id']}/commit")
    # Second upload of the same file → all should be skip-existing
    preview2 = _upload(client)
    assert preview2["will_create"] == 0
    assert preview2["will_skip_existing"] == 758
