"""ERA 835 payment-posting service.

build_preview() is pure (no DB writes) — it classifies each EraClaim into a
status and returns the plan. The commit step in the router does all writes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from sqlalchemy.orm import Session

from app.models.claim import Claim
from app.models.payment import Payment
from app.parsers.era_835 import EraClaim, EraFile


CLP01_PATTERN = re.compile(r"^\d+P\d+$")


MatchStatus = Literal[
    "matched", "unmatched", "cb_prefix_skipped",
    "reversal_flagged", "malformed_clp01", "already_posted",
]


@dataclass
class EraClaimMatch:
    era_claim: EraClaim
    status: MatchStatus
    internal_claim_id: Optional[str] = None
    matched_claim_id: Optional[str] = None   # our UUID as str
    reversal_reason: Optional[str] = None


@dataclass
class EraFilePreview:
    era: EraFile
    source_filename: str
    matches: List[EraClaimMatch] = field(default_factory=list)
    n_matched: int = 0
    n_unmatched: int = 0
    n_already_posted: int = 0
    n_cb_skipped: int = 0
    n_reversals: int = 0
    n_malformed: int = 0


def _has_negative_cas(era_claim: EraClaim) -> bool:
    for a in era_claim.adjustments:
        if a.amount < Decimal("0"):
            return True
    for svc in era_claim.service_lines:
        for a in svc.adjustments:
            if a.amount < Decimal("0"):
                return True
    return False


def _already_posted(db: Session, claim_id: str, era: EraFile,
                    era_claim: EraClaim) -> bool:
    """Return True iff a Payment already exists matching this (claim, ERA) tuple."""
    q = db.query(Payment).filter(
        Payment.claim_id == claim_id,
        Payment.check_number == era.check_number,
        Payment.amount == era_claim.paid_amount,
    )
    if era.check_date is not None:
        q = q.filter(Payment.payment_date == era.check_date)
    return q.first() is not None


def build_preview(db: Session, era: EraFile, source_filename: str) -> EraFilePreview:
    preview = EraFilePreview(era=era, source_filename=source_filename)
    for era_claim in era.claims:
        clp01 = era_claim.patient_control_number or ""
        clp07 = era_claim.payer_claim_number or ""

        if not CLP01_PATTERN.match(clp01):
            preview.matches.append(EraClaimMatch(
                era_claim=era_claim, status="malformed_clp01",
                internal_claim_id=clp01 or None,
            ))
            preview.n_malformed += 1
            continue

        if clp07.startswith("CB"):
            preview.matches.append(EraClaimMatch(
                era_claim=era_claim, status="cb_prefix_skipped",
                internal_claim_id=clp01,
            ))
            preview.n_cb_skipped += 1
            continue

        reversal_reason = None
        if era_claim.claim_status_code == "22":
            reversal_reason = "CLP02=22 (reversal of prior payment)"
        elif _has_negative_cas(era_claim):
            reversal_reason = "negative CAS adjustment amount"
        if reversal_reason:
            preview.matches.append(EraClaimMatch(
                era_claim=era_claim, status="reversal_flagged",
                internal_claim_id=clp01, reversal_reason=reversal_reason,
            ))
            preview.n_reversals += 1
            continue

        claim = db.query(Claim).filter(Claim.patient_control_number == clp01).first()
        if claim is None:
            preview.matches.append(EraClaimMatch(
                era_claim=era_claim, status="unmatched",
                internal_claim_id=clp01,
            ))
            preview.n_unmatched += 1
            continue

        if _already_posted(db, str(claim.id), era, era_claim):
            preview.matches.append(EraClaimMatch(
                era_claim=era_claim, status="already_posted",
                internal_claim_id=clp01, matched_claim_id=str(claim.id),
            ))
            preview.n_already_posted += 1
            continue

        preview.matches.append(EraClaimMatch(
            era_claim=era_claim, status="matched",
            internal_claim_id=clp01, matched_claim_id=str(claim.id),
        ))
        preview.n_matched += 1

    return preview


from datetime import date as date_cls
from app.models.audit import AuditLog
from app.models.claim import ClaimStatus, ServiceLine, ClaimAdjustment, ServiceLineAdjustment
from app.models.denial import Denial
from app.models.payment import PaymentType
from app.services.audit_service import log_action
from app.services.claim_math import recompute_balance
from app.utils.carc_codes import get_carc_info


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


def _has_real_denials(era_claim: EraClaim) -> bool:
    """Check if there are non-contractual denial adjustments."""
    from app.utils.carc_codes import get_carc_info, DenialCategory as CarcDenialCategory
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
    from app.utils.carc_codes import get_carc_info, DenialCategory as CarcDenialCategory
    from app.utils.maryland_rules import get_payer_rules
    from app.services.denial_analyzer import analyze_denial
    from app.models.denial import Denial, DenialStatus
    from datetime import date

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


def _update_claim_money(claim: Claim, era_claim: EraClaim) -> None:
    co45 = sum(
        a.amount for a in era_claim.adjustments
        if a.group_code == "CO" and a.reason_code == "45"
    )
    other = sum(
        a.amount for a in era_claim.adjustments
        if a.group_code not in ("CO", "PR")
    )
    claim.contractual_adjustment = (claim.contractual_adjustment or Decimal("0")) + co45
    claim.other_adjustment = (claim.other_adjustment or Decimal("0")) + other
    claim.patient_responsibility = era_claim.patient_responsibility
    claim.allowed_amount = era_claim.billed_amount - co45


def _post_claim_adjustments(db: Session, claim_id: str, era_file_id: str,
                            era_claim: EraClaim) -> None:
    """Create ClaimAdjustment rows. Dedup on (claim, era_file, group, reason)."""
    for adj in era_claim.adjustments:
        exists = db.query(ClaimAdjustment).filter(
            ClaimAdjustment.claim_id == claim_id,
            ClaimAdjustment.group_code == adj.group_code,
            ClaimAdjustment.reason_code == adj.reason_code,
        ).first()
        if exists:
            continue
        carc = get_carc_info(adj.reason_code)
        db.add(ClaimAdjustment(
            claim_id=claim_id,
            group_code=adj.group_code,
            reason_code=adj.reason_code,
            amount=adj.amount,
            quantity=adj.quantity,
            reason_description=carc.description,
        ))


def _post_service_lines(db: Session, claim_id: str, era_claim: EraClaim,
                        warnings: list) -> None:
    """Best-effort match by procedure_code + first modifier."""
    for svc in era_claim.service_lines:
        candidates = db.query(ServiceLine).filter(
            ServiceLine.claim_id == claim_id,
            ServiceLine.procedure_code == svc.procedure_code,
        ).all()
        chosen = None
        if len(candidates) == 1:
            chosen = candidates[0]
        elif len(candidates) > 1 and svc.modifier_1:
            mod_match = [c for c in candidates if c.modifier_1 == svc.modifier_1]
            if len(mod_match) == 1:
                chosen = mod_match[0]
        if chosen is None:
            warnings.append(f"service line {svc.procedure_code} not uniquely matched on claim")
            continue
        chosen.paid_amount = (chosen.paid_amount or Decimal("0")) + svc.paid_amount
        co45 = sum(a.amount for a in svc.adjustments
                   if a.group_code == "CO" and a.reason_code == "45")
        contractual = sum(a.amount for a in svc.adjustments if a.group_code == "CO")
        pr_sum = sum(a.amount for a in svc.adjustments if a.group_code == "PR")
        chosen.contractual_adjustment = (chosen.contractual_adjustment or Decimal("0")) + contractual
        chosen.patient_responsibility = pr_sum
        chosen.allowed_amount = svc.billed_amount - co45
        # Adjustments at line level
        for adj in svc.adjustments:
            carc = get_carc_info(adj.reason_code)
            db.add(ServiceLineAdjustment(
                service_line_id=chosen.id,
                group_code=adj.group_code,
                reason_code=adj.reason_code,
                amount=adj.amount,
                quantity=adj.quantity,
                reason_description=carc.description,
            ))


def post_claim(db: Session, match: EraClaimMatch, era: EraFile,
               era_file_row: "EraFileModel", user_email: Optional[str]) -> dict:
    """Post an ERA claim onto an existing Claim row.

    Assumes caller has already created the EraFile DB row (era_file_row).
    Writes Payment, updates Claim, creates ClaimAdjustment + Denial rows.
    """
    from app.models.claim import EraFile as EraFileModel  # noqa

    claim = db.query(Claim).filter(Claim.id == match.matched_claim_id).first()
    era_claim = match.era_claim

    # 1. Create Payment row
    pmt = Payment(
        claim_id=claim.id,
        patient_id=claim.patient_id,
        payment_type=PaymentType.INSURANCE_PAYMENT,
        amount=era_claim.paid_amount,
        payment_date=era.check_date or date_cls.today(),
        date_of_service=claim.date_of_service_from,
        payer_name=era.payer_name,
        check_number=era.check_number,
        era_file_id=era_file_row.id,
        posted_by=user_email or "era-poster",
    )
    db.add(pmt)
    db.flush()

    # 2. Update claim money + status
    _update_claim_money(claim, era_claim)
    if claim.payer_claim_number is None:
        claim.payer_claim_number = era_claim.payer_claim_number
    claim.check_number = era.check_number
    claim.check_date = era.check_date
    claim.era_file_id = era_file_row.id
    claim.status = _determine_claim_status(era_claim)
    # Sum all payments → claim.paid_amount
    total_paid = db.query(Payment).filter(Payment.claim_id == claim.id).all()
    claim.paid_amount = sum((p.amount for p in total_paid), Decimal("0"))

    # 3. Adjustments
    _post_claim_adjustments(db, str(claim.id), str(era_file_row.id), era_claim)

    # 4. Service lines
    warnings: list = []
    _post_service_lines(db, str(claim.id), era_claim, warnings)

    # 5. Denials (reuse legacy helper)
    if era_claim.is_denied or _has_real_denials(era_claim):
        _create_denials(db, claim, era_claim, era)

    # 6. Recompute balance
    recompute_balance(claim)

    db.commit()
    db.refresh(claim)

    log_action(
        db, "POST_PAYMENT", "claim",
        resource_id=str(claim.id),
        patient_id=str(claim.patient_id) if claim.patient_id else None,
        user_name=user_email,
        new_values={
            "paid_amount": float(claim.paid_amount or 0),
            "status": claim.status.value if claim.status else None,
            "check_number": claim.check_number,
        },
        description=f"ERA {match.internal_claim_id} check {era.check_number}",
    )
    return {"warnings": warnings}


@dataclass
class ProcessResult:
    era_file_id: Optional[str]
    claims_posted: int
    claims_already_posted: int
    claims_unmatched: int
    claims_reversal_flagged: int
    claims_cb_skipped: int
    claims_malformed: int
    payments_created: int
    denials_created: int
    errors: List[Dict[str, Any]]
    parse_errors: List[str]


def process_era_file(
    db: Session,
    content: str,
    filename: str,
    user_email: Optional[str],
) -> ProcessResult:
    """Parse + match + post one ERA file end-to-end.

    Callers: era_posting.commit (per EraFilePreview), waystar.sync_eras_sftp
    (per downloaded file). Creates an EraFile DB row, posts each matched
    claim in its own transaction, returns structured counts + errors.
    """
    from app.parsers.era_835 import Era835Parser
    from app.models.claim import EraFile as EraFileModel
    from app.models.denial import Denial

    try:
        era = Era835Parser().parse(content, filename=filename)
    except Exception as exc:
        return ProcessResult(
            era_file_id=None, claims_posted=0, claims_already_posted=0,
            claims_unmatched=0, claims_reversal_flagged=0, claims_cb_skipped=0,
            claims_malformed=0, payments_created=0, denials_created=0,
            errors=[{"filename": filename,
                     "message": f"parse failed: {type(exc).__name__}: {exc}"}],
            parse_errors=[],
        )

    preview = build_preview(db, era, source_filename=filename)

    era_file_row = EraFileModel(
        filename=filename, file_path="",
        payer_name=era.payer_name, payer_id=era.payer_id,
        check_number=era.check_number, check_date=era.check_date,
        check_amount=era.check_amount,
        transaction_count=len(era.claims),
        status="processed" if not era.parse_errors else "partial",
        error_log="\n".join(era.parse_errors) if era.parse_errors else None,
        imported_by=user_email or "era-poster",
    )
    db.add(era_file_row); db.commit(); db.refresh(era_file_row)

    denials_before = db.query(Denial).count()

    claims_posted = payments_created = 0
    claims_already_posted = claims_unmatched = 0
    claims_reversal_flagged = claims_cb_skipped = claims_malformed = 0
    errors: List[Dict[str, Any]] = []

    for m in preview.matches:
        if m.status == "matched":
            try:
                post_claim(db, m, era, era_file_row, user_email=user_email)
                claims_posted += 1
                payments_created += 1
            except Exception as exc:
                db.rollback()
                errors.append({"internal_claim_id": m.internal_claim_id,
                               "message": f"{type(exc).__name__}: {exc}"})
        elif m.status == "already_posted":   claims_already_posted += 1
        elif m.status == "unmatched":         claims_unmatched += 1
        elif m.status == "reversal_flagged":  claims_reversal_flagged += 1
        elif m.status == "cb_prefix_skipped": claims_cb_skipped += 1
        elif m.status == "malformed_clp01":   claims_malformed += 1

    denials_created = db.query(Denial).count() - denials_before

    log_action(db, "IMPORT", "era_file",
               resource_id=str(era_file_row.id), user_name=user_email,
               description=f"{filename} — {claims_posted} posted, {claims_unmatched} unmatched")

    return ProcessResult(
        era_file_id=str(era_file_row.id),
        claims_posted=claims_posted,
        claims_already_posted=claims_already_posted,
        claims_unmatched=claims_unmatched,
        claims_reversal_flagged=claims_reversal_flagged,
        claims_cb_skipped=claims_cb_skipped,
        claims_malformed=claims_malformed,
        payments_created=payments_created,
        denials_created=denials_created,
        errors=errors,
        parse_errors=list(era.parse_errors or []),
    )
