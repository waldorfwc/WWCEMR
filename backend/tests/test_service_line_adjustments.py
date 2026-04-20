"""Tests for service-line-level adjustment CRUD."""
from decimal import Decimal
from app.models.claim import Claim, ServiceLine, ServiceLineAdjustment, ClaimStatus
from app.models.audit import AuditLog


def _seed(db):
    c = Claim(claim_number="C-SLA", status=ClaimStatus.PENDING,
              billed_amount=Decimal("100"), balance=Decimal("100"))
    db.add(c); db.commit(); db.refresh(c)
    sl = ServiceLine(claim_id=c.id, procedure_code="99213",
                     units=Decimal("1"), billed_amount=Decimal("50"))
    db.add(sl); db.commit(); db.refresh(sl)
    return c, sl


def test_post_sl_adjustment_creates(client, db):
    _, sl = _seed(db)
    r = client.post(f"/api/service-lines/{sl.id}/adjustments", json={
        "group_code": "PR", "reason_code": "1", "amount": 20,
        "reason_description": "Deductible",
    })
    assert r.status_code == 201, r.text
    assert r.json()["group_code"] == "PR"
    assert float(r.json()["amount"]) == 20.0


def test_post_sl_adjustment_does_not_change_claim_balance(client, db):
    c, sl = _seed(db)  # claim.balance = 100
    client.post(f"/api/service-lines/{sl.id}/adjustments",
                json={"group_code": "PR", "reason_code": "1", "amount": 20})
    db.refresh(c)
    assert float(c.balance) == 100.0


def test_post_sl_adjustment_missing_line_404(client, db):
    r = client.post("/api/service-lines/00000000-0000-0000-0000-000000000000/adjustments",
                    json={"group_code": "PR", "reason_code": "1", "amount": 1})
    assert r.status_code == 404


def test_patch_sl_adjustment_updates(client, db):
    _, sl = _seed(db)
    adj = ServiceLineAdjustment(service_line_id=sl.id, group_code="CO",
                                reason_code="45", amount=Decimal("10"))
    db.add(adj); db.commit(); db.refresh(adj)
    r = client.patch(f"/api/service-line-adjustments/{adj.id}",
                     json={"amount": 12, "reason_description": "updated"})
    assert r.status_code == 200
    assert float(r.json()["amount"]) == 12.0
    assert r.json()["reason_description"] == "updated"


def test_patch_sl_adjustment_missing_404(client, db):
    r = client.patch("/api/service-line-adjustments/00000000-0000-0000-0000-000000000000",
                     json={"amount": 1})
    assert r.status_code == 404


def test_delete_sl_adjustment_removes(client, db):
    _, sl = _seed(db)
    adj = ServiceLineAdjustment(service_line_id=sl.id, group_code="CO",
                                reason_code="45", amount=Decimal("10"))
    db.add(adj); db.commit(); db.refresh(adj)
    r = client.delete(f"/api/service-line-adjustments/{adj.id}")
    assert r.status_code == 200
    assert db.query(ServiceLineAdjustment).filter(
        ServiceLineAdjustment.id == adj.id).first() is None


def test_delete_sl_adjustment_missing_404(client, db):
    r = client.delete("/api/service-line-adjustments/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_sl_adjustment_audit_rows_written(client, db):
    _, sl = _seed(db)
    r = client.post(f"/api/service-lines/{sl.id}/adjustments",
                    json={"group_code": "PR", "reason_code": "1", "amount": 20})
    adj_id = r.json()["id"]
    client.patch(f"/api/service-line-adjustments/{adj_id}", json={"amount": 21})
    client.delete(f"/api/service-line-adjustments/{adj_id}")
    actions = [a.action for a in db.query(AuditLog).filter(
        AuditLog.resource_type == "service_line_adjustment",
        AuditLog.resource_id == adj_id,
    ).order_by(AuditLog.timestamp).all()]
    assert actions == ["CREATE", "UPDATE", "DELETE"]


def test_sl_adjustments_forbidden_for_clinical(clinical_client, db):
    _, sl = _seed(db)
    assert clinical_client.post(
        f"/api/service-lines/{sl.id}/adjustments",
        json={"group_code": "PR", "reason_code": "1", "amount": 1}
    ).status_code == 403


def test_sl_adjustment_audit_includes_user_and_patient(client, db):
    """HIPAA traceability — audit rows must carry user_name and patient_id."""
    from app.models.patient import Patient
    p = Patient(patient_id="P-SLA-AUD", first_name="Sla", last_name="User")
    db.add(p); db.commit(); db.refresh(p)
    c, sl = _seed(db)
    c.patient_id = p.id
    db.commit()

    r = client.post(f"/api/service-lines/{sl.id}/adjustments",
                    json={"group_code": "PR", "reason_code": "1", "amount": 20})
    adj_id = r.json()["id"]

    entry = db.query(AuditLog).filter(
        AuditLog.resource_type == "service_line_adjustment",
        AuditLog.action == "CREATE",
        AuditLog.resource_id == adj_id,
    ).first()
    assert entry is not None
    assert entry.user_name == "tester@waldorfwomenscare.com"
    assert entry.patient_id == str(p.id)
