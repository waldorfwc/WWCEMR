"""Tests for the expanded PATCH /api/claims/{claim_id} endpoint."""
from datetime import date
from decimal import Decimal
from app.models.claim import Claim, ClaimStatus, InsuranceOrder
from app.models.patient import Patient
from app.models.audit import AuditLog


def _seed_claim(db, **overrides) -> Claim:
    c = Claim(
        claim_number="C0001",
        status=ClaimStatus.PENDING,
        billed_amount=Decimal("100"),
        contractual_adjustment=Decimal("10"),
        paid_amount=Decimal("80"),
        patient_responsibility=Decimal("5"),
        balance=Decimal("5"),
    )
    for k, v in overrides.items():
        setattr(c, k, v)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_patch_money_fields_recomputes_balance(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={"billed_amount": 200})
    assert r.status_code == 200, r.text
    body = r.json()
    # 200 - 10 - 0 - 80 - 5 = 105
    assert body["balance"] == 105.0


def test_patch_balance_in_body_is_ignored(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={"balance": 999})
    assert r.status_code == 200
    # Unchanged money fields → balance still 100-10-0-80-5 = 5
    assert r.json()["balance"] == 5.0


def test_patch_accepts_routing_fields(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={
        "payer_name": "Aetna", "subscriber_id": "SUB123",
        "group_number": "G1", "insurance_order": "secondary",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["payer_name"] == "Aetna"
    assert body["subscriber_id"] == "SUB123"
    assert body["group_number"] == "G1"
    assert body["insurance_order"] == "secondary"


def test_patch_accepts_date_fields(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={
        "date_of_service_from": "2026-01-15",
        "date_of_service_to": "2026-01-15",
        "check_date": "2026-02-01",
        "check_number": "CHK42",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["date_of_service_from"] == "2026-01-15"
    assert body["check_number"] == "CHK42"


def test_patch_accepts_identifiers_and_provider(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={
        "claim_number": "C9999",
        "payer_claim_number": "PCN-1",
        "rendering_provider_name": "Dr Example",
        "rendering_provider_npi": "1234567890",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["claim_number"] == "C9999"
    assert body["payer_claim_number"] == "PCN-1"
    assert body["rendering_provider_name"] == "Dr Example"
    assert body["rendering_provider_npi"] == "1234567890"


def test_patch_accepts_status_and_notes(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={
        "status": "paid", "notes": "manual review done",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "paid"
    assert body["notes"] == "manual review done"


def test_patch_bad_status_enum_422(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={"status": "not_a_status"})
    assert r.status_code == 422


def test_patch_bad_insurance_order_enum_422(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={"insurance_order": "quaternary"})
    assert r.status_code == 422


def test_patch_nonexistent_patient_id_422(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}",
                     json={"patient_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 422


def test_patch_valid_patient_id_succeeds(client, db):
    c = _seed_claim(db)
    p = Patient(patient_id="P001", first_name="A", last_name="B")
    db.add(p)
    db.commit()
    db.refresh(p)
    r = client.patch(f"/api/claims/{c.id}", json={"patient_id": str(p.id)})
    assert r.status_code == 200
    assert r.json()["patient_id"] == str(p.id)


def test_patch_missing_claim_404(client, db):
    r = client.patch("/api/claims/00000000-0000-0000-0000-000000000000",
                     json={"notes": "x"})
    assert r.status_code == 404


def test_patch_audit_row_has_changed_fields_only(client, db):
    c = _seed_claim(db)
    client.patch(f"/api/claims/{c.id}", json={"notes": "hello"})
    entries = db.query(AuditLog).filter(
        AuditLog.resource_type == "claim",
        AuditLog.action == "UPDATE",
        AuditLog.resource_id == str(c.id),
    ).all()
    assert len(entries) == 1
    e = entries[0]
    assert set(e.new_values.keys()) == {"notes"}
    assert e.new_values["notes"] == "hello"
    assert "notes" in e.old_values
    # HIPAA traceability: user_name and patient_id on audit rows
    assert e.user_name == "tester@waldorfwomenscare.com"  # TEST_USER from conftest
    # patient_id is None here because the seeded claim has no patient
    assert e.patient_id is None


def test_patch_audit_row_includes_patient_id_when_claim_has_patient(client, db):
    from app.models.patient import Patient
    p = Patient(patient_id="P-AUD", first_name="Au", last_name="Dit")
    db.add(p); db.commit(); db.refresh(p)
    c = _seed_claim(db, patient_id=p.id)
    client.patch(f"/api/claims/{c.id}", json={"notes": "traceable"})
    entry = db.query(AuditLog).filter(
        AuditLog.resource_type == "claim",
        AuditLog.action == "UPDATE",
        AuditLog.resource_id == str(c.id),
    ).first()
    assert entry is not None
    assert entry.patient_id == str(p.id)


def test_patch_forbidden_for_clinical(clinical_client, db):
    c = _seed_claim(db)
    r = clinical_client.patch(f"/api/claims/{c.id}", json={"notes": "x"})
    assert r.status_code == 403


# ============================ Phase 2d tests ============================
def test_patch_updates_follow_up_date(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={"follow_up_date": "2026-03-15"})
    assert r.status_code == 200, r.text
    assert r.json()["follow_up_date"] == "2026-03-15"
    db.refresh(c)
    assert c.follow_up_date == date(2026, 3, 15)


def test_patch_updates_follow_up_reason(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}",
                     json={"follow_up_reason": "2-Claim Sent <15D"})
    assert r.status_code == 200
    assert r.json()["follow_up_reason"] == "2-Claim Sent <15D"


def test_patch_updates_claim_state(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}", json={"claim_state": "Closed"})
    assert r.status_code == 200
    assert r.json()["claim_state"] == "Closed"


def test_patch_updates_last_submission_date(client, db):
    c = _seed_claim(db)
    r = client.patch(f"/api/claims/{c.id}",
                     json={"last_submission_date": "2026-01-10"})
    assert r.status_code == 200
    assert r.json()["last_submission_date"] == "2026-01-10"
