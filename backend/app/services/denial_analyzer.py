"""
Denial Analyzer — categorizes denials, determines appealability,
calculates deadlines, and recommends actions.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from sqlalchemy.orm import Session

from app.models.claim import Claim
from app.models.denial import Denial, DenialCategory, DenialStatus
from app.utils.carc_codes import get_carc_info, get_rarc_description, DenialCategory as CarcCategory
from app.utils.maryland_rules import get_payer_rules, calculate_appeal_deadline


# Map carc category to denial model category
_CATEGORY_MAP = {
    CarcCategory.TIMELY_FILING: DenialCategory.TIMELY_FILING,
    CarcCategory.AUTHORIZATION: DenialCategory.AUTHORIZATION,
    CarcCategory.MEDICAL_NECESSITY: DenialCategory.MEDICAL_NECESSITY,
    CarcCategory.ELIGIBILITY: DenialCategory.ELIGIBILITY,
    CarcCategory.DUPLICATE: DenialCategory.DUPLICATE,
    CarcCategory.CODING: DenialCategory.CODING,
    CarcCategory.COB: DenialCategory.COB,
    CarcCategory.PROVIDER_CREDENTIALING: DenialCategory.PROVIDER_CREDENTIALING,
    CarcCategory.MISSING_INFORMATION: DenialCategory.MISSING_INFORMATION,
    CarcCategory.BENEFIT_LIMIT: DenialCategory.BENEFIT_LIMIT,
    CarcCategory.NON_COVERED: DenialCategory.NON_COVERED,
    CarcCategory.CONTRACTUAL: DenialCategory.NON_COVERED,
    CarcCategory.OTHER: DenialCategory.OTHER,
}

# CARC codes where CO-45 style contractual adjustments are NOT denials
CONTRACTUAL_WRITE_OFF_CODES = {"45", "44", "23", "36", "24"}


def analyze_denial(
    claim: Claim,
    carc_code: str,
    rarc_code: Optional[str],
    group_code: str,
    denied_amount: Decimal,
    denial_date: Optional[date] = None,
    db: Optional[Session] = None,
) -> dict:
    """
    Analyze a denial and return structured recommendation.
    Returns a dict suitable for creating/updating a Denial record.
    """
    carc_info = get_carc_info(carc_code)
    rarc_desc = get_rarc_description(rarc_code) if rarc_code else ""

    denial_date = denial_date or date.today()
    dos = claim.date_of_service_from or denial_date

    payer_rules = get_payer_rules(
        payer_name=claim.payer_name or "",
        payer_id=claim.payer_id or "",
    )

    # Calculate deadlines
    appeal_deadline = calculate_appeal_deadline(denial_date, payer_rules, level=1)

    # Days until deadline
    days_to_deadline = (appeal_deadline - date.today()).days
    deadline_urgent = days_to_deadline <= 30

    # Timely filing check
    if carc_info.category == CarcCategory.TIMELY_FILING:
        tf_deadline = dos + timedelta(days=payer_rules.timely_filing_days)
        days_over_tf = (date.today() - tf_deadline).days
        write_off = days_over_tf > 180 or denied_amount < Decimal("50")
        recommended_action = "write_off" if write_off else "appeal"
        action_note = (
            f"Timely filing deadline was {tf_deadline.strftime('%m/%d/%Y')} for {payer_rules.payer_name}. "
            f"To appeal: gather clearinghouse acceptance report, delivery confirmation, and any prior submission records. "
            f"Maryland law requires payer to accept proof of timely submission (MD Insurance Article §15-1005)."
        )
    else:
        write_off = carc_info.write_off_recommended
        recommended_action = carc_info.recommended_action
        action_note = carc_info.action_notes

    category = _CATEGORY_MAP.get(carc_info.category, DenialCategory.OTHER)

    return {
        "carc_code": carc_code,
        "rarc_code": rarc_code,
        "group_code": group_code,
        "carc_description": carc_info.description,
        "rarc_description": rarc_desc,
        "category": category,
        "denied_amount": denied_amount,
        "denial_date": denial_date,
        "appeal_deadline": appeal_deadline,
        "appeal_level": 1,
        "appealable": carc_info.appealable and not write_off,
        "write_off_recommended": write_off,
        "write_off_reason": action_note if write_off else None,
        "recommended_action": recommended_action,
        "notes": (
            f"CARC {carc_code}: {carc_info.description}\n"
            f"{'RARC ' + rarc_code + ': ' + rarc_desc if rarc_code else ''}\n"
            f"Payer rules: {payer_rules.payer_name} — {payer_rules.notes}\n"
            f"Appeal deadline: {appeal_deadline.strftime('%m/%d/%Y')} "
            f"({'URGENT: ' if deadline_urgent else ''}{days_to_deadline} days)\n"
            f"Recommended action: {recommended_action.replace('_', ' ').title()}\n"
            f"{action_note}"
        ).strip(),
        "payer_rules": {
            "payer_name": payer_rules.payer_name,
            "timely_filing_days": payer_rules.timely_filing_days,
            "appeal_deadline_days": payer_rules.appeal_deadline_days,
            "notes": payer_rules.notes,
        },
        "deadline_urgent": deadline_urgent,
        "days_to_deadline": days_to_deadline,
    }


def get_denial_summary(db: Session) -> dict:
    """Dashboard-level denial summary."""
    from sqlalchemy import func
    from app.models.denial import Denial

    total = db.query(func.count(Denial.id)).scalar()
    open_denials = db.query(func.count(Denial.id)).filter(Denial.status == DenialStatus.OPEN).scalar()
    total_denied = db.query(func.sum(Denial.denied_amount)).filter(Denial.status == DenialStatus.OPEN).scalar() or 0

    # Urgent — deadline within 30 days
    urgent = db.query(func.count(Denial.id)).filter(
        Denial.status == DenialStatus.OPEN,
        Denial.appeal_deadline <= date.today() + timedelta(days=30),
        Denial.appeal_deadline >= date.today(),
    ).scalar()

    overdue = db.query(func.count(Denial.id)).filter(
        Denial.status == DenialStatus.OPEN,
        Denial.appeal_deadline < date.today(),
    ).scalar()

    by_category = {}
    rows = db.query(Denial.category, func.count(Denial.id), func.sum(Denial.denied_amount)).filter(
        Denial.status == DenialStatus.OPEN
    ).group_by(Denial.category).all()
    for row in rows:
        cat_key = row[0].value if hasattr(row[0], "value") else str(row[0])
        by_category[cat_key] = {"count": row[1], "amount": float(row[2] or 0)}

    return {
        "total": total,
        "open": open_denials,
        "total_denied_amount": float(total_denied),
        "urgent": urgent,
        "overdue": overdue,
        "by_category": by_category,
    }
