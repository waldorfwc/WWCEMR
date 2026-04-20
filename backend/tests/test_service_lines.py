"""Tests for service-line CRUD endpoints."""
from decimal import Decimal
from app.models.claim import Claim, ServiceLine, ServiceLineAdjustment, ClaimStatus
from app.models.audit import AuditLog


def _seed_claim(db) -> Claim:
    c = Claim(
        claim_number="C-SL",
        status=ClaimStatus.PENDING,
        billed_amount=Decimal("100"),
        balance=Decimal("100"),
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _seed_line(db, claim_id) -> ServiceLine:
    sl = ServiceLine(
        claim_id=claim_id,
        procedure_code="99213",
        units=Decimal("1"),
        billed_amount=Decimal("50"),
    )
    db.add(sl); db.commit(); db.refresh(sl)
    return sl


def test_post_service_line_full_fields(client, db):
    c = _seed_claim(db)
    r = client.post(f"/api/claims/{c.id}/service-lines", json={
        "procedure_code": "99213",
        "modifier_1": "25",
        "revenue_code": "0450",
        "units": 2,
        "description": "visit",
        "date_of_service_from": "2026-01-15",
        "date_of_service_to": "2026-01-15",
        "billed_amount": 150,
        "allowed_amount": 120,
        "paid_amount": 100,
        "patient_responsibility": 20,
        "contractual_adjustment": 30,
        "other_adjustment": 0,
        "diagnosis_codes": ["Z00.00", "E11.9"],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["procedure_code"] == "99213"
    assert body["modifier_1"] == "25"
    assert float(body["billed_amount"]) == 150.0
    assert body["date_of_service_from"] == "2026-01-15"
    assert "id" in body


def test_post_service_line_empty_body_creates_blank(client, db):
    c = _seed_claim(db)
    r = client.post(f"/api/claims/{c.id}/service-lines", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["procedure_code"] is None


def test_post_service_line_missing_claim_404(client, db):
    r = client.post("/api/claims/00000000-0000-0000-0000-000000000000/service-lines",
                    json={"procedure_code": "99213"})
    assert r.status_code == 404


def test_patch_service_line_updates_fields(client, db):
    c = _seed_claim(db)
    sl = _seed_line(db, c.id)
    r = client.patch(f"/api/service-lines/{sl.id}",
                     json={"modifier_1": "59", "units": 3, "billed_amount": 75})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["modifier_1"] == "59"
    assert float(body["units"]) == 3.0
    assert float(body["billed_amount"]) == 75.0


def test_patch_service_line_missing_404(client, db):
    r = client.patch("/api/service-lines/00000000-0000-0000-0000-000000000000",
                     json={"units": 2})
    assert r.status_code == 404


def test_delete_service_line_cascades_adjustments(client, db):
    c = _seed_claim(db)
    sl = _seed_line(db, c.id)
    # seed two SL adjustments
    db.add_all([
        ServiceLineAdjustment(service_line_id=sl.id, group_code="CO",
                              reason_code="45", amount=Decimal("10")),
        ServiceLineAdjustment(service_line_id=sl.id, group_code="PR",
                              reason_code="1", amount=Decimal("5")),
    ])
    db.commit()

    r = client.delete(f"/api/service-lines/{sl.id}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert db.query(ServiceLine).filter(ServiceLine.id == sl.id).first() is None
    assert db.query(ServiceLineAdjustment).filter(
        ServiceLineAdjustment.service_line_id == sl.id).count() == 0


def test_delete_service_line_missing_404(client, db):
    r = client.delete("/api/service-lines/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_service_line_writes_audit_rows_per_op(client, db):
    c = _seed_claim(db)
    # POST
    r = client.post(f"/api/claims/{c.id}/service-lines",
                    json={"procedure_code": "99213"})
    assert r.status_code == 201
    new_id = r.json()["id"]

    # PATCH
    client.patch(f"/api/service-lines/{new_id}", json={"units": 2})

    # DELETE
    client.delete(f"/api/service-lines/{new_id}")

    actions = [a.action for a in db.query(AuditLog).filter(
        AuditLog.resource_type == "service_line",
        AuditLog.resource_id == new_id,
    ).order_by(AuditLog.timestamp).all()]
    assert actions == ["CREATE", "UPDATE", "DELETE"]


def test_service_line_post_recomputes_parent_balance(client, db):
    c = _seed_claim(db)  # billed=100, balance=100
    # Change claim money first so balance ≠ default
    c.paid_amount = Decimal("40")
    db.commit()
    # Posting an SL should NOT change the claim money; balance should
    # re-settle to billed - paid = 60 on recompute.
    client.post(f"/api/claims/{c.id}/service-lines", json={"procedure_code": "99213"})
    db.refresh(c)
    assert float(c.balance) == 60.0


def test_service_lines_forbidden_for_clinical(clinical_client, db):
    c = _seed_claim(db)
    r = clinical_client.post(f"/api/claims/{c.id}/service-lines", json={})
    assert r.status_code == 403
    sl = _seed_line(db, c.id)
    assert clinical_client.patch(f"/api/service-lines/{sl.id}",
                                 json={"units": 2}).status_code == 403
    assert clinical_client.delete(f"/api/service-lines/{sl.id}").status_code == 403


def test_service_line_audit_includes_user_and_patient(client, db):
    """HIPAA traceability — audit rows must carry user_name and patient_id."""
    from app.models.patient import Patient
    p = Patient(patient_id="P-SL-AUD", first_name="Ser", last_name="Line")
    db.add(p); db.commit(); db.refresh(p)
    c = _seed_claim(db)
    c.patient_id = p.id
    db.commit()

    r = client.post(f"/api/claims/{c.id}/service-lines",
                    json={"procedure_code": "99214"})
    new_id = r.json()["id"]

    entry = db.query(AuditLog).filter(
        AuditLog.resource_type == "service_line",
        AuditLog.action == "CREATE",
        AuditLog.resource_id == new_id,
    ).first()
    assert entry is not None
    assert entry.user_name == "tester@waldorfwomenscare.com"
    assert entry.patient_id == str(p.id)
