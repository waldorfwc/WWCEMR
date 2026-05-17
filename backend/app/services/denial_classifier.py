"""Classify CARC/RARC adjustment codes: appealable vs informational, plus
suggested resolution paths.

Used to:
  - Decide whether a claim auto-routes to the Denials queue
  - Show "Issue: ... Suggested resolution: ..." banners on the claim detail
  - Recommend an appeal template type
"""
from __future__ import annotations

from typing import Dict, List, Optional


# CARC codes that represent legitimate (non-appealable) adjustments
# CO-45 = contractual write-off, PR-1/2/3 = patient responsibility, etc.
NON_APPEALABLE_CARC = {
    "1",   # Deductible (PR-1)
    "2",   # Coinsurance (PR-2)
    "3",   # Co-payment (PR-3)
    "45",  # Charge exceeds fee schedule (contractual write-off)
    "253", # Sequestration adjustment
}


# Map of appealable CARC codes → (issue summary, resolution hint, suggested template)
APPEALABLE_CARC: Dict[str, dict] = {
    "11": {
        "issue": "Diagnosis inconsistent with the procedure",
        "resolution": "Verify dx code accuracy. If correct, submit a Coding appeal with documentation showing dx-procedure linkage.",
        "template": "coding",
    },
    "16": {
        "issue": "Claim/service lacks information or has submission/billing error(s)",
        "resolution": "Identify the missing info from the RARC code, correct the claim, and resubmit with an explanation.",
        "template": "missing_info",
    },
    "18": {
        "issue": "Exact duplicate claim/service",
        "resolution": "Verify if this is a true duplicate. If not, submit appeal with documentation showing distinct services.",
        "template": "general",
    },
    "22": {
        "issue": "Care may be covered by another payer per coordination of benefits",
        "resolution": "Provide the patient's COB statement and primary payer's EOB. Resubmit with COB clarification.",
        "template": "cob",
    },
    "23": {
        "issue": "Impact of prior payer's adjudication",
        "resolution": "Check primary payer's EOB. If primary processed correctly, file COB appeal with secondary.",
        "template": "cob",
    },
    "27": {
        "issue": "Expenses incurred after coverage terminated",
        "resolution": "Verify coverage dates. If service was within coverage period, submit appeal with proof of active eligibility.",
        "template": "benefits",
    },
    "29": {
        "issue": "Time limit for filing has expired (timely filing)",
        "resolution": "Submit Timely Filing appeal with proof of original submission (clearinghouse receipt + timestamp).",
        "template": "timely_filing",
    },
    "31": {
        "issue": "Patient cannot be identified as our insured",
        "resolution": "Verify member ID and patient demographics. Resubmit with corrected member info.",
        "template": "missing_info",
    },
    "39": {
        "issue": "Services denied at time auth/precert was requested",
        "resolution": "Provide clinical documentation supporting medical necessity. Submit appeal.",
        "template": "medical_necessity",
    },
    "50": {
        "issue": "Service not deemed medically necessary",
        "resolution": "Submit Medical Necessity appeal with chart notes, dx justification, and clinical findings.",
        "template": "medical_necessity",
    },
    "55": {
        "issue": "Procedure/treatment is deemed experimental/investigational",
        "resolution": "Submit appeal citing peer-reviewed literature and applicable LCDs/NCDs supporting the procedure.",
        "template": "medical_necessity",
    },
    "96": {
        "issue": "Non-covered charge(s)",
        "resolution": "Verify benefit coverage. If service should be covered, submit Benefits appeal with policy reference.",
        "template": "benefits",
    },
    "97": {
        "issue": "Service/benefit included in payment for another service already billed (bundling)",
        "resolution": "If services are distinct, add modifier 25/59/XS/XU/XE/XP and resubmit. Otherwise, submit Bundling appeal.",
        "template": "unbundling",
    },
    "109": {
        "issue": "Claim/service not covered by this payer/contractor",
        "resolution": "Verify correct payer. If billed correctly, file COB appeal.",
        "template": "cob",
    },
    "146": {
        "issue": "Diagnosis was invalid for the date(s) of service reported",
        "resolution": "Verify dx code validity for DOS. Correct and resubmit.",
        "template": "coding",
    },
    "151": {
        "issue": "Payment adjusted because the payer deems the information submitted does not support this many/frequency of services",
        "resolution": "Submit Medical Necessity appeal with documentation supporting service frequency.",
        "template": "medical_necessity",
    },
    "167": {
        "issue": "Diagnosis is not covered",
        "resolution": "Verify dx code. If correct, submit Coverage appeal with policy reference.",
        "template": "benefits",
    },
    "197": {
        "issue": "Precertification/authorization/notification absent",
        "resolution": "Provide retro-auth if available. Otherwise, submit Medical Necessity appeal documenting urgency.",
        "template": "medical_necessity",
    },
    "204": {
        "issue": "Service/equipment/drug not covered under the patient's current benefit plan",
        "resolution": "Verify policy effective dates and benefits. Submit Benefits appeal with EOC/SOB reference.",
        "template": "benefits",
    },
    "242": {
        "issue": "Services not provided by network/primary care providers",
        "resolution": "If provider was in-network at time of service, submit appeal with credentialing proof.",
        "template": "general",
    },
}


# Common RARC codes that indicate fixable errors
RARC_HINTS: Dict[str, str] = {
    "M16": "Alert — please submit corrected claim",
    "M76": "Missing/incomplete/invalid diagnosis or condition",
    "M127": "Missing patient medical record for this service",
    "N115": "This decision was based on a Local Coverage Determination (LCD)",
    "N522": "Duplicate of a claim already submitted",
}


def is_appealable(group_code: str, reason_code: str) -> bool:
    """A line's adjustment is 'appealable' when it represents a denial we
    can dispute (CO-50, CO-29, etc.) — NOT a contractual write-off or
    legitimate patient responsibility (CO-45, PR-1, PR-2, PR-3)."""
    if not reason_code:
        return False
    rc = str(reason_code).strip()
    if rc in NON_APPEALABLE_CARC:
        return False
    return rc in APPEALABLE_CARC


def get_resolution_hint(reason_code: str) -> Optional[dict]:
    """Return {issue, resolution, template} for an appealable code, or None."""
    return APPEALABLE_CARC.get(str(reason_code).strip())


def summarize_claim_denials(lines: List[dict]) -> dict:
    """Walk a claim's service_lines and produce a denial summary.

    Returns:
      {
        has_appealable_denials: bool,
        denial_codes: [{group_code, reason_code, total_amount, lines_affected,
                        issue, resolution, template, appealable}],
        suggested_template: str | None,
        total_denied_amount: float,
      }
    """
    code_totals: Dict[str, dict] = {}
    total_denied = 0.0
    has_appealable = False
    suggested_template = None

    for ln in lines or []:
        adj_codes = ln.get("adjustment_codes") or []
        for ac in adj_codes:
            gc = (ac.get("group_code") or "").strip().upper()
            rc = (ac.get("reason_code") or "").strip()
            amt = float(ac.get("amount") or 0)
            key = f"{gc}-{rc}"
            if key not in code_totals:
                hint = get_resolution_hint(rc) if gc == "CO" else None
                appealable = is_appealable(gc, rc) if gc == "CO" else False
                code_totals[key] = {
                    "group_code": gc,
                    "reason_code": rc,
                    "total_amount": 0.0,
                    "lines_affected": 0,
                    "issue":      hint["issue"]      if hint else None,
                    "resolution": hint["resolution"] if hint else None,
                    "template":   hint["template"]   if hint else None,
                    "appealable": appealable,
                }
            code_totals[key]["total_amount"] += amt
            code_totals[key]["lines_affected"] += 1
            if code_totals[key]["appealable"]:
                has_appealable = True
                total_denied += amt
                if suggested_template is None:
                    suggested_template = code_totals[key]["template"]

    # Sort: appealable first (highest amount), then non-appealable
    sorted_codes = sorted(
        code_totals.values(),
        key=lambda c: (-1 if c["appealable"] else 1, -c["total_amount"]),
    )

    return {
        "has_appealable_denials": has_appealable,
        "denial_codes":           sorted_codes,
        "suggested_template":     suggested_template,
        "total_denied_amount":    total_denied,
    }
