"""Tests for the one-time claim-data wipe script."""
from decimal import Decimal
import pytest
from app.models.claim import Claim, ServiceLine, ClaimAdjustment, ServiceLineAdjustment, EraFile, ClaimStatus
from app.models.denial import Denial, DenialCategory, DenialStatus
from app.models.appeal import Appeal, AppealStatus
from app.models.audit import AuditLog
from app.models.patient import Patient
from app.models.user import User, UserGroup
from app.scripts.reset_claims_data import run


def _seed_all(db):
    # Claim-side rows that SHOULD be wiped
    c = Claim(claim_number="C1", status=ClaimStatus.PENDING, balance=Decimal("0"))
    db.add(c); db.commit(); db.refresh(c)

    sl = ServiceLine(claim_id=c.id, procedure_code="99213")
    db.add(sl); db.commit(); db.refresh(sl)

    db.add(ClaimAdjustment(claim_id=c.id, group_code="CO", reason_code="45", amount=Decimal("10")))
    db.add(ServiceLineAdjustment(service_line_id=sl.id, group_code="PR", reason_code="1", amount=Decimal("5")))

    d = Denial(claim_id=c.id, carc_code="16", category=DenialCategory.MISSING_INFORMATION, status=DenialStatus.OPEN, denied_amount=Decimal("10"))
    db.add(d); db.commit(); db.refresh(d)

    db.add(Appeal(denial_id=d.id, status=AppealStatus.DRAFT))
    db.add(EraFile(filename="x.835", file_path="/tmp/x.835"))

    # Audit rows: some that SHOULD be wiped, some that should survive
    db.add(AuditLog(action="UPDATE", resource_type="claim", resource_id=str(c.id)))
    db.add(AuditLog(action="UPDATE", resource_type="service_line", resource_id=str(sl.id)))
    db.add(AuditLog(action="DELETE", resource_type="denial", resource_id=str(d.id)))
    db.add(AuditLog(action="IMPORT", resource_type="charge_analysis_file", resource_id="abc"))
    db.add(AuditLog(action="USER_UPDATED", resource_type="user", resource_id="x@y.z"))  # survives
    db.add(AuditLog(action="VIEW", resource_type="document", resource_id="doc1"))        # survives

    # Non-claim rows that MUST survive
    db.add(Patient(patient_id="P001", first_name="Sur", last_name="Vive"))
    db.add(User(email="survivor@waldorfwomenscare.com", group=UserGroup.ADMIN))
    db.commit()


def test_wipe_deletes_claim_side_data_preserves_others(db):
    _seed_all(db)
    counts = run(confirm=True, session=db)

    assert db.query(Claim).count() == 0
    assert db.query(ServiceLine).count() == 0
    assert db.query(ClaimAdjustment).count() == 0
    assert db.query(ServiceLineAdjustment).count() == 0
    assert db.query(Denial).count() == 0
    assert db.query(Appeal).count() == 0
    assert db.query(EraFile).count() == 0

    # Audit wiped only for targeted resource_types
    surviving_types = {row.resource_type for row in db.query(AuditLog).all()}
    assert "claim" not in surviving_types
    assert "service_line" not in surviving_types
    assert "denial" not in surviving_types
    assert "charge_analysis_file" not in surviving_types
    assert "user" in surviving_types
    assert "document" in surviving_types

    # Non-claim tables untouched
    assert db.query(Patient).count() == 1
    assert db.query(User).count() == 1

    # Returned counts dict has the expected keys
    assert counts["claims"] >= 1
    assert counts["service_lines"] >= 1
    assert counts["audit_log"] >= 4
    assert "patients" not in counts  # proof the script never touched Patient


def test_wipe_refuses_without_confirm_flag(db):
    _seed_all(db)
    with pytest.raises(SystemExit):
        run(confirm=False, session=db)
    assert db.query(Claim).count() == 1


def test_wipe_is_idempotent(db):
    _seed_all(db)
    run(confirm=True, session=db)
    counts2 = run(confirm=True, session=db)
    assert all(v == 0 for v in counts2.values())
