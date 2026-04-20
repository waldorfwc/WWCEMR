"""Tests for claim-level adjustment CRUD."""
from decimal import Decimal
from app.models.claim import Claim, ClaimAdjustment, ClaimStatus
from app.models.audit import AuditLog


def _seed_claim(db) -> Claim:
    c = Claim(claim_number="C-ADJ", status=ClaimStatus.PENDING,
              billed_amount=Decimal("100"), balance=Decimal("100"))
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_post_claim_adjustment_creates_row(client, db):
    c = _seed_claim(db)
    r = client.post(f"/api/claims/{c.id}/adjustments", json={
        "group_code": "CO",
        "reason_code": "45",
        "amount": 25,
        "reason_description": "Charge exceeds fee schedule",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["group_code"] == "CO"
    assert body["reason_code"] == "45"
    assert float(body["amount"]) == 25.0
    assert "id" in body


def test_post_claim_adjustment_does_not_change_claim_balance(client, db):
    c = _seed_claim(db)  # balance=100 (no other money edits)
    client.post(f"/api/claims/{c.id}/adjustments",
                json={"group_code": "CO", "reason_code": "45", "amount": 25})
    db.refresh(c)
    # Key freeform-behavior assertion: adjustment CRUD must not touch balance.
    assert float(c.balance) == 100.0


def test_post_claim_adjustment_missing_claim_404(client, db):
    r = client.post("/api/claims/00000000-0000-0000-0000-000000000000/adjustments",
                    json={"group_code": "CO", "reason_code": "45", "amount": 1})
    assert r.status_code == 404


def test_patch_claim_adjustment_updates_fields(client, db):
    c = _seed_claim(db)
    adj = ClaimAdjustment(claim_id=c.id, group_code="CO", reason_code="45",
                          amount=Decimal("10"))
    db.add(adj); db.commit(); db.refresh(adj)
    r = client.patch(f"/api/claim-adjustments/{adj.id}",
                     json={"reason_code": "97", "amount": 15,
                           "reason_description": "Not covered"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reason_code"] == "97"
    assert float(body["amount"]) == 15.0
    assert body["reason_description"] == "Not covered"


def test_patch_claim_adjustment_missing_404(client, db):
    r = client.patch("/api/claim-adjustments/00000000-0000-0000-0000-000000000000",
                     json={"amount": 1})
    assert r.status_code == 404


def test_delete_claim_adjustment_removes_row(client, db):
    c = _seed_claim(db)
    adj = ClaimAdjustment(claim_id=c.id, group_code="CO",
                          reason_code="45", amount=Decimal("10"))
    db.add(adj); db.commit(); db.refresh(adj)
    r = client.delete(f"/api/claim-adjustments/{adj.id}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert db.query(ClaimAdjustment).filter(ClaimAdjustment.id == adj.id).first() is None


def test_delete_claim_adjustment_missing_404(client, db):
    r = client.delete("/api/claim-adjustments/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_claim_adjustment_audit_rows_written(client, db):
    c = _seed_claim(db)
    r = client.post(f"/api/claims/{c.id}/adjustments",
                    json={"group_code": "CO", "reason_code": "45", "amount": 10})
    adj_id = r.json()["id"]
    client.patch(f"/api/claim-adjustments/{adj_id}", json={"amount": 12})
    client.delete(f"/api/claim-adjustments/{adj_id}")
    actions = [a.action for a in db.query(AuditLog).filter(
        AuditLog.resource_type == "claim_adjustment",
        AuditLog.resource_id == adj_id,
    ).order_by(AuditLog.timestamp).all()]
    assert actions == ["CREATE", "UPDATE", "DELETE"]


def test_claim_adjustments_forbidden_for_clinical(clinical_client, db):
    c = _seed_claim(db)
    assert clinical_client.post(
        f"/api/claims/{c.id}/adjustments",
        json={"group_code": "CO", "reason_code": "45", "amount": 1}
    ).status_code == 403


def test_claim_adjustment_audit_includes_user_and_patient(client, db):
    """HIPAA traceability — audit rows must carry user_name and patient_id."""
    from app.models.patient import Patient
    p = Patient(patient_id="P-ADJ-AUD", first_name="Adj", last_name="User")
    db.add(p); db.commit(); db.refresh(p)
    c = _seed_claim(db)
    c.patient_id = p.id
    db.commit()

    r = client.post(f"/api/claims/{c.id}/adjustments",
                    json={"group_code": "CO", "reason_code": "45", "amount": 10})
    adj_id = r.json()["id"]

    entry = db.query(AuditLog).filter(
        AuditLog.resource_type == "claim_adjustment",
        AuditLog.action == "CREATE",
        AuditLog.resource_id == adj_id,
    ).first()
    assert entry is not None
    assert entry.user_name == "tester@waldorfwomenscare.com"
    assert entry.patient_id == str(p.id)
