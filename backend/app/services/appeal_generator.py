"""
Appeal Letter Generator using Claude API.
Generates Maryland-specific, payer-specific appeal letters
for denied claims.
"""

import anthropic
from datetime import date
from decimal import Decimal
from typing import Optional

from app.config import settings
from app.models.claim import Claim
from app.models.denial import Denial
from app.utils.maryland_rules import get_payer_rules, MARYLAND_PROMPT_PAYMENT


def _build_timely_filing_context(denial: Denial, claim: Claim) -> str:
    rules = get_payer_rules(claim.payer_name or "", claim.payer_id or "")
    return f"""
TIMELY FILING APPEAL CONTEXT:
- Payer: {claim.payer_name}
- Payer timely filing limit: {rules.timely_filing_days} days from date of service
- Date of Service: {claim.date_of_service_from}
- Denial Reason Code: {denial.carc_code} — {denial.carc_description}
- Maryland Law: {MARYLAND_PROMPT_PAYMENT['statute']} requires payers to process clean claims
  within 30 days (electronic) or 45 days (paper). Maryland law also requires payers to accept
  proof of timely submission including clearinghouse records and electronic delivery confirmations.
- Key appeal argument: Provider must demonstrate the claim was submitted within the timely filing
  window, using clearinghouse acceptance reports, EDI acknowledgment records, or prior submissions.
"""


def _build_context(denial: Denial, claim: Claim, patient_name: str, practice_info: dict) -> str:
    dos_str = claim.date_of_service_from.strftime("%m/%d/%Y") if claim.date_of_service_from else "Unknown"
    denial_date_str = denial.denial_date.strftime("%m/%d/%Y") if denial.denial_date else date.today().strftime("%m/%d/%Y")
    deadline_str = denial.appeal_deadline.strftime("%m/%d/%Y") if denial.appeal_deadline else "N/A"

    extra = ""
    if denial.category and denial.category.value == "timely_filing":
        extra = _build_timely_filing_context(denial, claim)

    return f"""
CLAIM INFORMATION:
- Patient: {patient_name}
- Date of Service: {dos_str}
- Claim Number: {claim.claim_number}
- Payer Claim Number: {claim.payer_claim_number or 'N/A'}
- Insurance: {claim.payer_name}
- Insurance Member ID: {claim.subscriber_id or 'N/A'}
- Billed Amount: ${claim.billed_amount:,.2f}
- Denied Amount: ${denial.denied_amount:,.2f}
- Denial Date: {denial_date_str}
- Appeal Deadline: {deadline_str}

DENIAL INFORMATION:
- CARC Code: {denial.carc_code}
- CARC Description: {denial.carc_description}
- RARC Code: {denial.rarc_code or 'N/A'}
- RARC Description: {denial.rarc_description or 'N/A'}
- Group Code: {denial.group_code}
- Denial Category: {denial.category.value if denial.category else 'Other'}
- Appeal Level: {denial.appeal_level} (first-level appeal)

PRACTICE INFORMATION:
- Practice Name: {practice_info.get('name', 'Medical Practice')}
- NPI: {practice_info.get('npi', '')}
- Address: {practice_info.get('address', '')}
- Phone: {practice_info.get('phone', '')}
- State: Maryland

{extra}
"""


def generate_appeal_letter(
    denial: Denial,
    claim: Claim,
    patient_name: str,
    practice_info: dict,
    additional_notes: str = "",
) -> dict:
    """
    Generate an appeal letter using Claude API.
    Returns dict with 'subject', 'body', 'model_used'.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    context = _build_context(denial, claim, patient_name, practice_info)
    denial_category = denial.category.value if denial.category else "other"

    category_guidance = {
        "timely_filing": "Focus on proof of timely filing, clearinghouse records, and Maryland Insurance Article §15-1005. Request reconsideration based on documented submission records.",
        "authorization": "Focus on medical necessity, urgent/emergent circumstances if applicable, and request retro-authorization. Reference plan coverage criteria.",
        "medical_necessity": "Focus on clinical documentation, diagnosis-procedure alignment, peer-reviewed clinical guidelines, and treating physician attestation.",
        "eligibility": "Focus on enrollment verification, effective date confirmation, and request payer to verify coverage dates with employer or exchange.",
        "duplicate": "Focus on explaining why this is NOT a duplicate — different DOS, different service, or original claim was never paid.",
        "coding": "Focus on coding accuracy, appropriate modifier usage, and supporting the service as separately reimbursable.",
        "cob": "Explain coordination of benefits order, provide primary EOB if applicable.",
        "provider_credentialing": "Focus on provider's current in-network status, credentialing records, or network exception request.",
        "missing_information": "Provide the missing information directly in the appeal with supporting documentation.",
        "benefit_limit": "Request exceptions based on medical necessity or argue that limits have not been properly tracked.",
        "non_covered": "Argue coverage based on plan documents, medical necessity, or request plan exception.",
        "other": "Address the specific denial reason with supporting clinical and administrative documentation.",
    }

    guidance = category_guidance.get(denial_category, category_guidance["other"])

    prompt = f"""You are an expert medical billing appeals specialist writing a formal appeal letter for a denied insurance claim in Maryland.

{context}

APPEAL STRATEGY: {guidance}

ADDITIONAL NOTES FROM BILLING STAFF: {additional_notes or 'None'}

Write a professional, formal appeal letter that:
1. Clearly identifies the claim and denial being appealed
2. States the specific grounds for appeal
3. Cites relevant plan language, state law (Maryland Insurance Article), or clinical guidelines where applicable
4. Makes a compelling argument for payment
5. Lists specific supporting documentation being attached
6. Requests a specific resolution (overturn denial, pay claim in full)
7. Includes a professional closing with contact information placeholder

For timely filing denials specifically:
- Reference Maryland Insurance Article §15-1005 prompt payment law
- Reference NAIC model regulations on timely filing
- Request the payer provide their specific written timely filing policy
- Note that electronic clearinghouse records (X12 277CA acknowledgment) constitute proof of timely submission

Format the letter with:
- Date at top
- Payer address block
- RE: line with claim details
- Formal salutation
- Body paragraphs
- Professional closing
- Signature block

Use [PLACEHOLDER] for any information that needs to be filled in (e.g., specific document dates, tracking numbers).

Keep the tone professional, factual, and assertive — not aggressive. Reference specific policy provisions where known.

Return ONLY the letter text, no additional commentary."""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2000,
        system="You are an expert medical billing specialist with deep knowledge of Maryland insurance law, HIPAA regulations, and claim appeal procedures. Write precise, professional appeal letters that maximize the chance of claim overturn.",
        messages=[
            {"role": "user", "content": prompt}
        ],
    )

    letter_body = response.content[0].text

    # Generate subject line
    subject = (
        f"APPEAL: {denial.carc_description or 'Denied Claim'} — "
        f"Patient: {patient_name} — "
        f"DOS: {claim.date_of_service_from.strftime('%m/%d/%Y') if claim.date_of_service_from else 'Unknown'} — "
        f"Claim: {claim.claim_number}"
    )

    return {
        "subject": subject,
        "body": letter_body,
        "model_used": "claude-opus-4-6",
        "denial_category": denial_category,
        "tokens_used": response.usage.input_tokens + response.usage.output_tokens,
    }


def generate_appeal_letter_sync(
    denial: Denial,
    claim: Claim,
    patient_name: str,
    practice_info: dict,
    additional_notes: str = "",
) -> dict:
    """Synchronous wrapper for appeal letter generation."""
    return generate_appeal_letter(denial, claim, patient_name, practice_info, additional_notes)
