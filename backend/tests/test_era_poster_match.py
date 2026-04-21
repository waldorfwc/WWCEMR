"""Matcher tests for era_poster."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ClaimStatus, InsuranceOrder
from app.models.patient import Patient
from app.models.payment import Payment, PaymentType
from app.parsers.era_835 import Era835Parser, EraClaim, EraAdjustment
from app.services.era_poster import build_preview


FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def _era_from_fixture():
    return Era835Parser().parse(FIXTURE.read_text(), filename=FIXTURE.name)


def _claim(db, pcn: str, billed="253.76"):
    p = Patient(patient_id=pcn.split("P")[1], first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    c = Claim(
        claim_number="V", patient_id=p.id, patient_control_number=pcn,
        billed_amount=Decimal(billed),
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("0"),
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_match_strict_by_patient_control_number(db):
    _claim(db, "216059P45740")
    era = _era_from_fixture()
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    matched = [m for m in preview.matches if m.status == "matched"]
    assert len(matched) == 1
    assert matched[0].internal_claim_id == "216059P45740"


def test_match_unmatched_when_no_link(db):
    era = _era_from_fixture()
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    assert preview.n_matched == 0
    assert preview.n_unmatched == len(era.claims)


def test_match_malformed_clp01_skipped(db):
    era = _era_from_fixture()
    era.claims[0].patient_control_number = "NOTFORMATTED"
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    statuses = [m.status for m in preview.matches]
    assert "malformed_clp01" in statuses


def test_match_cb_prefix_in_clp07_skipped(db):
    era = _era_from_fixture()
    era.claims[0].payer_claim_number = "CBABC123"
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    skipped = [m for m in preview.matches if m.status == "cb_prefix_skipped"]
    assert len(skipped) == 1


def test_reversal_flagged_on_clp02_22(db):
    _claim(db, "216059P45740")
    era = _era_from_fixture()
    era.claims[0].claim_status_code = "22"
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    flagged = [m for m in preview.matches if m.status == "reversal_flagged"]
    assert any(m.internal_claim_id == "216059P45740" for m in flagged)
    assert "CLP02=22" in flagged[0].reversal_reason


def test_reversal_flagged_on_negative_cas(db):
    _claim(db, "216059P45740")
    era = _era_from_fixture()
    era.claims[0].adjustments.append(
        EraAdjustment(group_code="CO", reason_code="45", amount=Decimal("-50")))
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    flagged = [m for m in preview.matches if m.status == "reversal_flagged"]
    assert any(m.internal_claim_id == "216059P45740" for m in flagged)
    assert "negative" in flagged[0].reversal_reason.lower()


def test_already_posted_when_payment_exists(db):
    c = _claim(db, "216059P45740")
    era = _era_from_fixture()
    # Pre-seed a Payment that would look like the ERA's posting
    era_claim = [x for x in era.claims if x.patient_control_number == "216059P45740"][0]
    db.add(Payment(
        claim_id=c.id, payment_type=PaymentType.INSURANCE_PAYMENT,
        amount=era_claim.paid_amount, payment_date=era.check_date,
        check_number=era.check_number, payer_name=era.payer_name,
    ))
    db.commit()
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    already = [m for m in preview.matches if m.status == "already_posted"]
    assert any(m.internal_claim_id == "216059P45740" for m in already)
