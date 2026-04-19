"""
ERA Import Service — takes a parsed EraFile and persists it to the database.
Handles claim matching, COB ordering, denial analysis, and payment posting.
"""

from decimal import Decimal
from datetime import date
from typing import Optional
from sqlalchemy.orm import Session

from app.models.claim import Claim, ClaimStatus, ServiceLine, ClaimAdjustment, ServiceLineAdjustment, EraFile as EraFileModel, InsuranceOrder
from app.models.payment import Payment, PaymentType
from app.models.denial import Denial, DenialStatus
from app.parsers.era_835 import EraFile, EraClaim
from app.services.denial_analyzer import analyze_denial
from app.utils.carc_codes import get_carc_info, get_rarc_description, DenialCategory as CarcDenialCategory
from app.utils.maryland_rules import get_payer_rules, calculate_appeal_deadline

# CARC codes that are contractual write-offs — not true denials
CONTRACTUAL_CODES = {"45", "44", "23", "24", "36"}
# CO-45 user said to ignore — skip creating denial record for these
SKIP_DENIAL_CODES = {"45"}


def _determine_claim_status(era_claim: EraClaim) -> ClaimStatus:
    code = era_claim.claim_status_code
    if code == "1":
        return ClaimStatus.PAID
    if code == "2":
        return ClaimStatus.ADJUSTED
    if code in ("3", "4"):
        return ClaimStatus.DENIED
    if era_claim.paid_amount > Decimal("0") and era_claim.paid_amount < era_claim.billed_amount:
        return ClaimStatus.PARTIAL
    return ClaimStatus.PENDING


def _map_insurance_order(claim_filing_indicator: str, payer_name: str) -> InsuranceOrder:
    """Heuristic — real systems would track COB on the claim record."""
    return InsuranceOrder.PRIMARY  # Default; UI allows manual override


def import_era_file(
    db: Session,
    era: EraFile,
    file_path: str,
    imported_by: str = "system",
) -> EraFileModel:
    """Persist an ERA file and all its claims to the database."""

    # Create ERA file record
    era_file = EraFileModel(
        filename=era.filename,
        file_path=file_path,
        payer_name=era.payer_name,
        payer_id=era.payer_id,
        check_number=era.check_number,
        check_date=era.check_date,
        check_amount=era.check_amount,
        transaction_count=len(era.claims),
        status="processed" if not era.parse_errors else "partial",
        error_log="\n".join(era.parse_errors) if era.parse_errors else None,
        imported_by=imported_by,
    )
    db.add(era_file)
    db.flush()

    for era_claim in era.claims:
        _import_claim(db, era_claim, era, era_file)

    db.commit()
    db.refresh(era_file)
    return era_file


def _import_claim(db: Session, era_claim: EraClaim, era: EraFile, era_file: EraFileModel):
    """Import a single claim from ERA into the database."""
    # Try to match existing claim by patient control number or payer claim number
    existing = None
    if era_claim.patient_control_number:
        existing = db.query(Claim).filter(
            Claim.claim_number == era_claim.patient_control_number
        ).first()
    if not existing and era_claim.payer_claim_number:
        existing = db.query(Claim).filter(
            Claim.payer_claim_number == era_claim.payer_claim_number
        ).first()

    # Calculate totals
    total_contractual = sum(
        a.amount for a in era_claim.adjustments if a.group_code == "CO" and a.reason_code != "45"
    )
    co45 = sum(
        a.amount for a in era_claim.adjustments if a.group_code == "CO" and a.reason_code == "45"
    )
    total_other = sum(
        a.amount for a in era_claim.adjustments if a.group_code not in ("CO", "PR")
    )
    allowed = era_claim.billed_amount - co45
    balance = era_claim.patient_responsibility - Decimal("0")  # Will be updated as payments come in

    status = _determine_claim_status(era_claim)

    if existing:
        # Update existing claim with ERA payment info
        existing.payer_claim_number = era_claim.payer_claim_number or existing.payer_claim_number
        existing.paid_amount = era_claim.paid_amount
        existing.allowed_amount = allowed
        existing.patient_responsibility = era_claim.patient_responsibility
        existing.contractual_adjustment = co45
        existing.other_adjustment = total_other
        existing.balance = balance
        existing.status = status
        existing.check_number = era.check_number
        existing.check_date = era.check_date
        existing.era_file_id = era_file.id
        existing.payer_name = existing.payer_name or era.payer_name
        existing.payer_id = existing.payer_id or era.payer_id
        claim = existing
    else:
        # Create new claim record from ERA data
        claim = Claim(
            claim_number=era_claim.patient_control_number,
            payer_claim_number=era_claim.payer_claim_number,
            patient_control_number=era_claim.patient_control_number,
            payer_name=era.payer_name,
            payer_id=era.payer_id,
            subscriber_id=era_claim.subscriber_id,
            group_number=era_claim.group_number,
            rendering_provider_npi=era_claim.rendering_provider_npi,
            rendering_provider_name=era_claim.rendering_provider_name,
            billed_amount=era_claim.billed_amount,
            allowed_amount=allowed,
            paid_amount=era_claim.paid_amount,
            patient_responsibility=era_claim.patient_responsibility,
            contractual_adjustment=co45,
            other_adjustment=total_other,
            balance=balance,
            status=status,
            claim_filing_indicator=era_claim.claim_filing_indicator,
            check_number=era.check_number,
            check_date=era.check_date,
            era_file_id=era_file.id,
            date_of_service_from=era_claim.statement_date_from,
            date_of_service_to=era_claim.statement_date_to,
            statement_date=era_claim.statement_date_from,
            insurance_order=InsuranceOrder.PRIMARY,
        )

        # Try to set patient name from ERA
        if era_claim.patient_last_name:
            # Patient matching would be done here in production
            pass

        db.add(claim)
        db.flush()

    # Add claim-level adjustments
    for adj in era_claim.adjustments:
        if not existing:  # Only add for new claims; updates would need dedup logic
            carc_info = get_carc_info(adj.reason_code)
            db_adj = ClaimAdjustment(
                claim_id=claim.id,
                group_code=adj.group_code,
                reason_code=adj.reason_code,
                amount=adj.amount,
                quantity=adj.quantity,
                reason_description=carc_info.description,
            )
            db.add(db_adj)

    # Add service lines
    if era_claim.service_lines and not existing:
        for svc in era_claim.service_lines:
            svc_co45 = sum(a.amount for a in svc.adjustments if a.group_code == "CO" and a.reason_code == "45")
            svc_contractual = sum(a.amount for a in svc.adjustments if a.group_code == "CO")
            svc_pr = sum(a.amount for a in svc.adjustments if a.group_code == "PR")

            db_svc = ServiceLine(
                claim_id=claim.id,
                procedure_code=svc.procedure_code,
                modifier_1=svc.modifier_1,
                modifier_2=svc.modifier_2,
                modifier_3=svc.modifier_3,
                modifier_4=svc.modifier_4,
                revenue_code=svc.revenue_code,
                billed_amount=svc.billed_amount,
                paid_amount=svc.paid_amount,
                allowed_amount=svc.billed_amount - svc_co45,
                patient_responsibility=svc_pr,
                contractual_adjustment=svc_contractual,
                units=svc.units,
                date_of_service_from=svc.date_from or era_claim.statement_date_from,
            )
            db.add(db_svc)
            db.flush()

            for adj in svc.adjustments:
                carc_info = get_carc_info(adj.reason_code)
                db.add(ServiceLineAdjustment(
                    service_line_id=db_svc.id,
                    group_code=adj.group_code,
                    reason_code=adj.reason_code,
                    amount=adj.amount,
                    quantity=adj.quantity,
                    reason_description=carc_info.description,
                ))

    # Create insurance payment record
    if era_claim.paid_amount > Decimal("0"):
        payment = Payment(
            claim_id=claim.id,
            payment_type=PaymentType.INSURANCE_PAYMENT,
            amount=era_claim.paid_amount,
            payment_date=era.check_date or date.today(),
            date_of_service=era_claim.statement_date_from,
            payer_name=era.payer_name,
            check_number=era.check_number,
            era_file_id=era_file.id,
            posted_by="ERA Import",
        )
        db.add(payment)

    # Create denial records for actual denials (not CO-45)
    if era_claim.is_denied or _has_real_denials(era_claim):
        _create_denials(db, claim, era_claim, era)

    db.flush()


def _has_real_denials(era_claim: EraClaim) -> bool:
    """Check if there are non-contractual denial adjustments."""
    for adj in era_claim.adjustments:
        if adj.group_code == "CO" and adj.reason_code not in SKIP_DENIAL_CODES:
            carc = get_carc_info(adj.reason_code)
            if carc.category not in (CarcDenialCategory.CONTRACTUAL,):
                return True
        if adj.group_code in ("OA", "PI") and adj.reason_code not in SKIP_DENIAL_CODES:
            return True
    for svc in era_claim.service_lines:
        for adj in svc.adjustments:
            if adj.group_code == "CO" and adj.reason_code not in SKIP_DENIAL_CODES:
                return True
    return False


def _create_denials(db: Session, claim: Claim, era_claim: EraClaim, era: EraFile):
    """Create denial records for a denied/partially denied claim."""
    payer_rules = get_payer_rules(era.payer_name, era.payer_id)
    denial_date = era.check_date or date.today()

    processed_codes = set()

    # Claim-level denials
    for adj in era_claim.adjustments:
        if adj.reason_code in SKIP_DENIAL_CODES:
            continue
        if adj.reason_code in processed_codes:
            continue
        if adj.group_code not in ("CO", "OA", "PI", "PR"):
            continue

        carc_info = get_carc_info(adj.reason_code)
        if carc_info.category == CarcDenialCategory.CONTRACTUAL:
            continue  # Pure contractual — not a denial

        analysis = analyze_denial(
            claim=claim,
            carc_code=adj.reason_code,
            rarc_code=era_claim.rarc_codes[0] if era_claim.rarc_codes else None,
            group_code=adj.group_code,
            denied_amount=adj.amount,
            denial_date=denial_date,
        )

        from app.models.denial import DenialCategory as ModelDenialCategory
        denial = Denial(
            claim_id=claim.id,
            carc_code=analysis["carc_code"],
            rarc_code=analysis.get("rarc_code"),
            group_code=analysis["group_code"],
            carc_description=analysis["carc_description"],
            rarc_description=analysis.get("rarc_description"),
            category=analysis["category"],
            denied_amount=analysis["denied_amount"],
            denial_date=analysis["denial_date"],
            status=DenialStatus.OPEN,
            appeal_deadline=analysis["appeal_deadline"],
            appeal_level=1,
            appealable=analysis["appealable"],
            write_off_recommended=analysis["write_off_recommended"],
            write_off_reason=analysis.get("write_off_reason"),
            recommended_action=analysis["recommended_action"],
            notes=analysis["notes"],
        )
        db.add(denial)
        processed_codes.add(adj.reason_code)
