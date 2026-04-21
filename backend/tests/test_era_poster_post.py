"""Posting tests for era_poster.post_claim()."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ClaimStatus, InsuranceOrder, EraFile as EraFileModel, ClaimAdjustment
from app.models.denial import Denial
from app.models.patient import Patient
from app.models.payment import Payment
from app.parsers.era_835 import Era835Parser, EraAdjustment
from app.services.era_poster import build_preview, post_claim

FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def _setup(db):
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
    era = Era835Parser().parse(FIXTURE.read_text(), filename=FIXTURE.name)
    era_file = EraFileModel(
        filename=FIXTURE.name, file_path=str(FIXTURE),
        payer_name=era.payer_name, check_number=era.check_number,
        check_date=era.check_date, check_amount=era.check_amount,
        transaction_count=len(era.claims), status="processed",
    )
    db.add(era_file); db.commit(); db.refresh(era_file)
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    match = [m for m in preview.matches if m.status == "matched"][0]
    return c, era, era_file, match


def test_post_creates_payment_row(db):
    c, era, era_file, match = _setup(db)
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    payments = db.query(Payment).filter(Payment.claim_id == c.id).all()
    assert len(payments) == 1
    assert payments[0].amount == match.era_claim.paid_amount
    assert payments[0].check_number == era.check_number


def test_post_updates_claim_paid_amount_from_payment_sum(db):
    c, era, era_file, match = _setup(db)
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    db.refresh(c)
    assert c.paid_amount == match.era_claim.paid_amount


def test_post_recomputes_balance(db):
    c, era, era_file, match = _setup(db)
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    db.refresh(c)
    # balance = billed - contractual - other - paid - pt_resp
    expected = (c.billed_amount - (c.contractual_adjustment or 0)
                - (c.other_adjustment or 0) - (c.paid_amount or 0)
                - (c.patient_responsibility or 0))
    assert c.balance == expected


def test_post_sets_status_from_clp02_1_paid(db):
    c, era, era_file, match = _setup(db)
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    db.refresh(c)
    assert c.status == ClaimStatus.PAID


def test_post_sets_payer_claim_number_when_null(db):
    c, era, era_file, match = _setup(db)
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    db.refresh(c)
    assert c.payer_claim_number == match.era_claim.payer_claim_number


def test_post_creates_claim_adjustments(db):
    c, era, era_file, match = _setup(db)
    match.era_claim.adjustments = [
        EraAdjustment(group_code="CO", reason_code="45", amount=Decimal("100")),
        EraAdjustment(group_code="PR", reason_code="1", amount=Decimal("20")),
    ]
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    adjs = db.query(ClaimAdjustment).filter(ClaimAdjustment.claim_id == c.id).all()
    assert len(adjs) == 2
    codes = {(a.group_code, a.reason_code) for a in adjs}
    assert codes == {("CO", "45"), ("PR", "1")}


def test_post_creates_denial_for_real_denial(db):
    c, era, era_file, match = _setup(db)
    # CO-16 is a real denial (missing information)
    match.era_claim.adjustments = [
        EraAdjustment(group_code="CO", reason_code="16", amount=Decimal("50")),
    ]
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    denials = db.query(Denial).filter(Denial.claim_id == c.id).all()
    assert len(denials) == 1
    assert denials[0].carc_code == "16"


def test_post_skips_denial_for_co_45(db):
    c, era, era_file, match = _setup(db)
    match.era_claim.adjustments = [
        EraAdjustment(group_code="CO", reason_code="45", amount=Decimal("50")),
    ]
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    denials = db.query(Denial).filter(Denial.claim_id == c.id).all()
    assert len(denials) == 0
