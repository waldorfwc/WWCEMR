"""
Maryland-specific insurance rules:
- Timely filing limits by payer
- Appeal deadlines by payer
- MD Insurance Article §15-1005 prompt payment requirements
- Appeal strategies specific to Maryland law
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass
class PayerRules:
    payer_name: str
    payer_ids: list[str]                    # Known payer IDs / EDI IDs
    timely_filing_days: int                  # Days from DOS to submit
    appeal_deadline_days: int                # Days from denial to appeal
    second_appeal_days: Optional[int]        # Days for 2nd-level appeal
    external_review_days: Optional[int]      # Days for external review (MD law)
    notes: str = ""
    write_off_threshold_days: int = 0        # Days past TF where write-off is recommended


# Maryland payer rules — sourced from payer contracts and MD Insurance Article
MARYLAND_PAYER_RULES: list[PayerRules] = [
    PayerRules(
        payer_name="Medicare",
        payer_ids=["00435", "00040", "12101", "00430", "NGSMEDI"],
        timely_filing_days=365,         # 12 months from DOS
        appeal_deadline_days=120,        # 120 days from MSN/EOB for redetermination
        second_appeal_days=180,          # 180 days for reconsideration
        external_review_days=60,
        notes="Medicare timely filing is 1 year from DOS. Secondary Medicare (MSP) allows 12 months from primary EOB date. Appeal levels: Redetermination → Reconsideration (QIC) → ALJ Hearing → MAC → Federal Court",
        write_off_threshold_days=365,
    ),
    PayerRules(
        payer_name="Maryland Medicaid (MCO)",
        payer_ids=["77039", "77350", "MARYMD", "21450"],
        timely_filing_days=365,         # 12 months from DOS
        appeal_deadline_days=30,         # 30 days from denial for MCO
        second_appeal_days=30,
        external_review_days=30,
        notes="MD Medicaid timely filing is 12 months from DOS. If secondary to Medicare, allow 6 months from Medicare payment date. MCO appeal must be filed within 30 days. State fair hearing available.",
        write_off_threshold_days=365,
    ),
    PayerRules(
        payer_name="CareFirst BlueCross BlueShield",
        payer_ids=["20260", "00650", "00840", "98000", "CAREFIRST"],
        timely_filing_days=180,
        appeal_deadline_days=180,
        second_appeal_days=60,
        external_review_days=45,
        notes="CareFirst allows 180 days from DOS for most plans. Federal plans (BCBS FEP) may have different limits — check plan documents. Appeal within 180 days of denial.",
        write_off_threshold_days=180,
    ),
    PayerRules(
        payer_name="Aetna",
        payer_ids=["60054", "91136", "60192", "AETNA"],
        timely_filing_days=180,
        appeal_deadline_days=180,
        second_appeal_days=60,
        external_review_days=45,
        notes="Aetna standard: 180 days from DOS. Some self-funded plans may differ. Aetna Better Health of Maryland (Medicaid) follows state Medicaid rules.",
        write_off_threshold_days=180,
    ),
    PayerRules(
        payer_name="United Healthcare",
        payer_ids=["87726", "87416", "52133", "UHC", "UNITED"],
        timely_filing_days=90,
        appeal_deadline_days=180,
        second_appeal_days=60,
        external_review_days=45,
        notes="UHC standard timely filing is 90 days. Some plans extend to 180 days. Check each plan's benefit document. Community Plan (Medicaid) follows state rules.",
        write_off_threshold_days=90,
    ),
    PayerRules(
        payer_name="Cigna",
        payer_ids=["62308", "36273", "67703", "CIGNA"],
        timely_filing_days=90,
        appeal_deadline_days=180,
        second_appeal_days=60,
        external_review_days=45,
        notes="Cigna standard: 90 days from DOS. Some plans allow 180 days. Always verify with specific plan documents. HealthSpring/Maryland HealthChoice follows Medicaid rules.",
        write_off_threshold_days=90,
    ),
    PayerRules(
        payer_name="Humana",
        payer_ids=["61101", "61110", "HUMANA"],
        timely_filing_days=365,
        appeal_deadline_days=180,
        second_appeal_days=60,
        external_review_days=45,
        notes="Humana generally allows 365 days (1 year) from DOS. Medicare Advantage plans follow Medicare timely filing rules.",
        write_off_threshold_days=365,
    ),
    PayerRules(
        payer_name="Kaiser Permanente",
        payer_ids=["94135", "KAISER"],
        timely_filing_days=90,
        appeal_deadline_days=60,
        second_appeal_days=30,
        external_review_days=45,
        notes="Kaiser Mid-Atlantic: 90 days timely filing for non-participating providers. Participating providers may have different terms.",
        write_off_threshold_days=90,
    ),
    PayerRules(
        payer_name="Johns Hopkins Employer Health Programs",
        payer_ids=["52214", "JHEHP"],
        timely_filing_days=180,
        appeal_deadline_days=180,
        second_appeal_days=60,
        external_review_days=45,
        notes="JHEHP: 180 days from DOS. Johns Hopkins Health Plans.",
        write_off_threshold_days=180,
    ),
    PayerRules(
        payer_name="Tricare",
        payer_ids=["84980", "TRICARE", "CHAMPUS"],
        timely_filing_days=365,
        appeal_deadline_days=90,
        second_appeal_days=90,
        external_review_days=None,
        notes="Tricare: 1 year from DOS for non-network providers. 6 months for network. Appeal within 90 days of EOB.",
        write_off_threshold_days=365,
    ),
    PayerRules(
        payer_name="Workers Compensation",
        payer_ids=["WC"],
        timely_filing_days=730,          # Generally 2 years
        appeal_deadline_days=180,
        second_appeal_days=None,
        external_review_days=None,
        notes="Maryland Workers' Comp: file with MWCC. 2-year statute of limitations. Billing follows MWCC fee schedule.",
        write_off_threshold_days=730,
    ),
]

# Default rules when payer is not specifically identified (Maryland mandate)
DEFAULT_RULES = PayerRules(
    payer_name="Unknown Payer (MD Default)",
    payer_ids=[],
    timely_filing_days=90,               # Conservative default
    appeal_deadline_days=180,            # MD Insurance Article §15-1004
    second_appeal_days=60,
    external_review_days=45,             # MD IARP (Independent Appeal Request Process)
    notes="Default Maryland rules. Verify specific plan documents. MD Insurance Article §15-1005 requires insurers to pay/deny within 30 days (electronic) or 45 days (paper). Interest accrues at 1.5%/month after deadline.",
)


# Maryland Prompt Payment Law — MD Insurance Article §15-1005
MARYLAND_PROMPT_PAYMENT = {
    "electronic_claim_days": 30,         # Must pay/deny within 30 days
    "paper_claim_days": 45,              # Must pay/deny within 45 days
    "interest_rate_monthly": 0.015,      # 1.5% per month on overdue claims
    "statute": "MD Insurance Article §15-1005",
    "note": "If payer fails to meet prompt payment, provider may file complaint with Maryland Insurance Administration (MIA) at www.insurance.maryland.gov",
}


def get_payer_rules(payer_name: str = "", payer_id: str = "") -> PayerRules:
    """Match payer by name or ID, return rules."""
    payer_name_upper = (payer_name or "").upper()
    payer_id_upper = (payer_id or "").upper()

    for rules in MARYLAND_PAYER_RULES:
        if payer_id_upper and payer_id_upper in [p.upper() for p in rules.payer_ids]:
            return rules
        if payer_name_upper:
            for keyword in rules.payer_name.upper().split():
                if len(keyword) > 3 and keyword in payer_name_upper:
                    return rules

    return DEFAULT_RULES


def calculate_timely_filing_deadline(dos: date, rules: PayerRules) -> date:
    return dos + timedelta(days=rules.timely_filing_days)


def calculate_appeal_deadline(denial_date: date, rules: PayerRules, level: int = 1) -> date:
    if level == 1:
        days = rules.appeal_deadline_days
    elif level == 2:
        days = rules.second_appeal_days or rules.appeal_deadline_days
    else:
        days = rules.external_review_days or 45
    return denial_date + timedelta(days=days)


def is_timely_filing_expired(dos: date, payer_name: str = "", payer_id: str = "") -> tuple[bool, PayerRules]:
    rules = get_payer_rules(payer_name, payer_id)
    deadline = calculate_timely_filing_deadline(dos, rules)
    return date.today() > deadline, rules


def get_write_off_recommendation(dos: date, denied_amount: float, payer_name: str = "", payer_id: str = "") -> dict:
    rules = get_payer_rules(payer_name, payer_id)
    deadline = calculate_timely_filing_deadline(dos, rules)
    days_since_dos = (date.today() - dos).days
    expired = date.today() > deadline
    days_over = (date.today() - deadline).days if expired else 0

    if expired and days_over > 180:
        return {
            "recommend_write_off": True,
            "reason": f"Timely filing expired {days_over} days ago (limit: {rules.timely_filing_days} days). Appeal is unlikely to succeed.",
            "rules": rules,
        }
    if expired:
        return {
            "recommend_write_off": False,
            "reason": f"Timely filing expired {days_over} days ago but appeal may still be possible. Gather proof of timely submission.",
            "rules": rules,
        }
    return {
        "recommend_write_off": False,
        "reason": f"Timely filing deadline is {deadline.strftime('%m/%d/%Y')} ({(deadline - date.today()).days} days remaining). Appeal recommended.",
        "rules": rules,
    }
