"""Commit tests for ERA posting."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from app.models.audit import AuditLog
from app.models.claim import Claim, ClaimStatus, InsuranceOrder, EraFile as EraFileModel
from app.models.patient import Patient
from app.models.payment import Payment
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def _link_one_claim(db):
    p = Patient(patient_id="45740", first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    c = Claim(
        claim_number="V1", patient_id=p.id,
        patient_control_number="216059P45740",
        billed_amount=Decimal("253.76"),
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("253.76"),
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _upload(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/imports/era-posting",
            files=[("file", (FIXTURE.name, f, "application/octet-stream"))],
        ).json()


def test_commit_posts_matched_claims(client, db):
    c = _link_one_claim(db)
    preview = _upload(client)
    assert preview["totals"]["n_matched"] == 1

    r = client.post(f"/api/imports/era-posting/{preview['session_id']}/commit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["claims_posted"] == 1
    assert body["payments_created"] == 1
    # One EraFile row created
    era_files = db.query(EraFileModel).all()
    assert len(era_files) == 1


def test_commit_writes_per_claim_audit(client, db):
    c = _link_one_claim(db)
    preview = _upload(client)
    client.post(f"/api/imports/era-posting/{preview['session_id']}/commit")
    audit = db.query(AuditLog).filter(
        AuditLog.action == "POST_PAYMENT",
        AuditLog.resource_type == "claim",
    ).all()
    assert len(audit) == 1
    assert audit[0].user_name == "tester@waldorfwomenscare.com"
    assert audit[0].patient_id == str(c.patient_id)


def test_commit_writes_top_level_import_audit(client, db):
    _link_one_claim(db)
    preview = _upload(client)
    client.post(f"/api/imports/era-posting/{preview['session_id']}/commit")
    audit = db.query(AuditLog).filter(
        AuditLog.resource_type == "era_file",
        AuditLog.action == "IMPORT",
    ).all()
    assert len(audit) == 1


def test_commit_404_on_unknown_session(client, db):
    r = client.post("/api/imports/era-posting/nope/commit")
    assert r.status_code == 404


def test_commit_forbidden_for_clinical(clinical_client, db):
    # A session in the store is not user-bound; a clinical user attempting
    # to commit any session must still be 403'd by the billing-router guard.
    now = datetime.now(timezone.utc)
    import_sessions._sessions.clear()
    import_sessions._sessions["sess-x"] = import_sessions.SessionEntry(
        session_id="sess-x", payload={"previews": []}, filename="f",
        file_path="/tmp/f", user_email="admin@x",
        created_at=now, expires_at=now + timedelta(minutes=30),
    )
    r = clinical_client.post("/api/imports/era-posting/sess-x/commit")
    assert r.status_code == 403
