"""Match tests for Claims Analysis bootstrap."""
from datetime import date
from decimal import Decimal
from app.models.claim import Claim, ClaimStatus, InsuranceOrder
from app.models.patient import Patient
from app.services.claims_analysis_matcher import (
    ClaimsAnalysisGroup, MatchResult, match_groups,
)


def _group(priority="primary", cid="C1", pid="P1", dos=date(2026, 1, 2), amount="100"):
    return ClaimsAnalysisGroup(
        patient_external_id=pid, claim_id=cid, dos=dos,
        total_amount=Decimal(amount), row_count=1,
        insurance_priority=priority, internal_claim_id=f"{cid}P{pid}",
    )


def _seed_patient(db, pid="P1"):
    p = Patient(patient_id=pid, first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    return p


def _seed_claim(db, patient, order=InsuranceOrder.PRIMARY, dos=date(2026, 1, 2),
                amount="100", pcn=None):
    c = Claim(
        claim_number="V1", patient_id=patient.id,
        date_of_service_from=dos, billed_amount=Decimal(amount),
        status=ClaimStatus.PENDING, insurance_order=order,
        balance=Decimal("0"), patient_control_number=pcn,
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_match_primary_will_patch_when_pcn_null(db):
    p = _seed_patient(db)
    c = _seed_claim(db, p)
    results = match_groups(db, [_group()])
    assert len(results) == 1
    r = results[0]
    assert r.status == "will_patch"
    assert r.matched_claim_id == str(c.id)


def test_match_primary_already_set_when_equal(db):
    p = _seed_patient(db)
    _seed_claim(db, p, pcn="C1PP1")
    r = match_groups(db, [_group()])[0]
    assert r.status == "already_set"


def test_match_primary_conflict_when_pcn_differs(db):
    p = _seed_patient(db)
    _seed_claim(db, p, pcn="OTHER999")
    r = match_groups(db, [_group()])[0]
    assert r.status == "conflict"
    assert r.conflict_existing_value == "OTHER999"


def test_match_primary_no_patient(db):
    r = match_groups(db, [_group(pid="GHOST")])[0]
    assert r.status == "no_patient"


def test_match_primary_no_claim(db):
    _seed_patient(db)
    r = match_groups(db, [_group(dos=date(2026, 2, 1))])[0]
    assert r.status == "no_claim"


def test_match_primary_ambiguous(db):
    p = _seed_patient(db)
    _seed_claim(db, p)
    _seed_claim(db, p)  # second claim, same patient+DOS+billed
    r = match_groups(db, [_group()])[0]
    assert r.status == "ambiguous"


def test_match_secondary_will_create_when_no_existing_secondary(db):
    p = _seed_patient(db)
    _seed_claim(db, p)  # primary exists
    r = match_groups(db, [_group(priority="secondary", cid="C2")])[0]
    assert r.status == "will_create_secondary"
    # matched_claim_id points at the PRIMARY (we copy from it on create)
    assert r.matched_claim_id is not None


def test_match_secondary_no_primary_means_no_claim(db):
    _seed_patient(db)  # patient exists but no primary claim
    r = match_groups(db, [_group(priority="secondary")])[0]
    assert r.status == "no_claim"


def test_match_secondary_already_set(db):
    p = _seed_patient(db)
    _seed_claim(db, p)  # primary
    _seed_claim(db, p, order=InsuranceOrder.SECONDARY, pcn="C2PP1")
    r = match_groups(db, [_group(priority="secondary", cid="C2")])[0]
    assert r.status == "already_set"
