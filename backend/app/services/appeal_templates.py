"""Appeal letter templates — body skeletons per (template_type, level).

Each template has a `subject` and a `body` with `{{tokens}}` substituted at
draft time. The body is meant as a STARTING POINT — Claude can rewrite it
into a more tailored argument, and the biller can edit before sending.

Tokens supported (case-sensitive):
  {{patient_name}}, {{patient_dob}}, {{patient_chart_number}}
  {{claim_number}}, {{dos}}, {{billed_amount}}, {{insurance_balance}}
  {{insurance_company}}, {{policy_number}}, {{group_number}}, {{plan_name}}
  {{cpt_codes}}, {{diagnosis_codes}}, {{denial_codes}}
  {{practice_name}}, {{practice_address}}, {{practice_phone}}, {{practice_npi}}, {{practice_tax_id}}
  {{level}}, {{level_label}}, {{prior_appeal_date}}
"""
from __future__ import annotations

from typing import Dict


TEMPLATE_TYPES: Dict[str, str] = {
    "medical_necessity":  "Medical Necessity",
    "timely_filing":      "Timely Filing",
    "cob":                "Coordination of Benefits",
    "unbundling":         "Bundling / Modifier",
    "missing_info":       "Missing Information / Corrected Claim",
    "benefits":           "Benefits / Coverage",
    "coding":             "Coding (Diagnosis/Procedure Mismatch)",
    "general":            "General Reconsideration",
}


LEVEL_LABEL = {
    1: "First-Level Appeal (Reconsideration)",
    2: "Second-Level Appeal (Formal Appeal)",
    3: "External Review (IRO/IDR)",
}


# ---------- subject lines ----------

def make_subject(template_type: str, level: int, claim_number: str, dos: str, patient_name: str) -> str:
    type_label = TEMPLATE_TYPES.get(template_type, "Reconsideration")
    level_str = "Level 1 Reconsideration" if level == 1 else "Level 2 Appeal" if level == 2 else "External Review"
    return f"{level_str} — {type_label} — Claim #{claim_number} — DOS {dos} — {patient_name}"


# ---------- body templates ----------
# Single string per template; {{tokens}} substituted by the renderer.

_BODY_HEADER = """\
{{practice_name}}
{{practice_address}}
Phone: {{practice_phone}}  |  NPI: {{practice_npi}}  |  Tax ID: {{practice_tax_id}}

{{today_date}}

{{recipient_name}}
{{recipient_address}}

RE: {{level_label}}
    Claim #: {{claim_number}}
    Patient: {{patient_name}}    DOB: {{patient_dob}}
    Member ID: {{policy_number}}    Group: {{group_number}}
    Date of Service: {{dos}}
    Plan: {{insurance_company}} — {{plan_name}}

To Whom It May Concern:

This letter is submitted as a {{level_label_inline}} on behalf of {{patient_name}} for
the above-referenced claim. We respectfully request that you reconsider your
adverse determination and process this claim for payment in the amount of
{{billed_amount}}.
"""


_BODIES = {
    # ─── MEDICAL NECESSITY ───
    "medical_necessity": _BODY_HEADER + """\

The denial cited the service as "not medically necessary" (denial code(s)
{{denial_codes}}). We disagree with this determination based on the following:

The services billed (CPT {{cpt_codes}}) were performed for diagnosis
{{diagnosis_codes}}, and were medically necessary based on the patient's
documented condition and clinical presentation. Supporting documentation,
including the chart note for the date of service and pertinent clinical
findings, is enclosed.

We affirm that:
  1. The services were ordered and performed by qualified providers.
  2. The services followed accepted standards of care for the diagnosis
     and clinical presentation.
  3. The diagnosis codes submitted accurately represent the patient's
     condition and support medical necessity for the procedures billed.

In light of the supporting documentation enclosed and the standards of care
referenced above, we request that the denial be overturned and the claim
processed for payment.
""",

    # ─── TIMELY FILING ───
    "timely_filing": _BODY_HEADER + """\

The denial cited the claim as untimely (denial code(s) {{denial_codes}}). We
respectfully dispute this determination.

The original claim was submitted electronically on
[INSERT ORIGINAL SUBMISSION DATE], well within your published timely-filing
window for this date of service. Proof of electronic submission, including
the clearinghouse receipt and submission timestamp, is enclosed.

If the original submission was rejected at the clearinghouse, payer, or front
end, we have no record of receiving such notification within the timely-filing
window. Per applicable claim-handling regulations, when a payer fails to
notify the provider of a rejection in time to permit refiling, the timely-filing
defense does not apply.

We request that the claim be reprocessed for payment based on the original
submission date.
""",

    # ─── COORDINATION OF BENEFITS ───
    "cob": _BODY_HEADER + """\

The denial cited an unresolved coordination-of-benefits (COB) issue (denial
code(s) {{denial_codes}}). We submit the following to resolve the COB
question:

Per our records, {{insurance_company}} is the patient's primary insurance for
the date of service. Enclosed are:
  - Patient's signed COB statement
  - Copy of the patient's primary insurance card on file
  - EOB from any prior payer (if applicable)

Based on the enclosed documentation, we respectfully request that you
reprocess this claim as primary and remit payment for the services rendered.
""",

    # ─── BUNDLING / MODIFIER ───
    "unbundling": _BODY_HEADER + """\

The denial cited the service as bundled or included in another procedure
(denial code(s) {{denial_codes}}). We respectfully disagree.

The procedures billed (CPT {{cpt_codes}}) were performed as separate, distinct,
and medically necessary services for the date of service. The appropriate
modifier(s) were applied to indicate the distinct nature of each procedure:

  - Modifier 25: Significant, separately identifiable E/M service on the
    same day as a procedure
  - Modifier 59: Distinct procedural service
  - Modifier LT/RT: Bilateral / lateral procedures

The services do not represent NCCI bundling violations because they were
performed on different anatomic sites, during different sessions, or for
different conditions, as documented in the medical record.

We request that the denial be overturned and the claim processed for payment
of the separately identifiable services performed.
""",

    # ─── MISSING INFO / CORRECTED CLAIM ───
    "missing_info": _BODY_HEADER + """\

The denial indicated missing or invalid information on the original claim
(denial code(s) {{denial_codes}}). We have corrected the deficiency and are
resubmitting the claim with this letter:

[BILLER: list specific corrections — e.g., updated diagnosis code,
corrected member ID, added authorization number, etc.]

The corrected claim is enclosed. Please process for payment based on the
revised information.
""",

    # ─── BENEFITS / COVERAGE ───
    "benefits": _BODY_HEADER + """\

The denial cited the service as not covered under the patient's plan (denial
code(s) {{denial_codes}}). We respectfully ask you to reconsider:

The services billed (CPT {{cpt_codes}}) for diagnosis {{diagnosis_codes}}
are covered services under the patient's plan based on the Evidence of
Coverage and Summary of Benefits as published by your plan for the date of
service.

If the denial reflects a recent benefits change, we request a copy of the
specific policy provision being applied. If the denial reflects a benefit
exclusion, we request reconsideration based on medical necessity (see
enclosed documentation).

We request that the claim be reprocessed as a covered service.
""",

    # ─── CODING ───
    "coding": _BODY_HEADER + """\

The denial cited a diagnosis-procedure inconsistency (denial code(s)
{{denial_codes}}). We respectfully disagree:

The diagnosis code(s) {{diagnosis_codes}} accurately represent(s) the
patient's clinical condition that necessitated the procedure(s) {{cpt_codes}}.
Both ICD-10-CM and CPT coding guidelines support this linkage:

[BILLER: cite specific coding guideline or LCD/NCD policy if applicable]

The medical record substantiates both the diagnosis and the procedure as
billed. We request that the claim be reprocessed for payment.
""",

    # ─── GENERAL RECONSIDERATION ───
    "general": _BODY_HEADER + """\

The above claim was denied on the basis of {{denial_codes}}. We respectfully
request reconsideration based on the following:

The services billed (CPT {{cpt_codes}}) for diagnosis {{diagnosis_codes}}
were medically necessary, properly documented, and correctly coded. We
believe the denial was issued in error.

We have enclosed the relevant documentation to support reconsideration.
Please review and process the claim for payment.
""",
}


_BODY_FOOTER = """\

{{additional_verbiage}}

If you require additional information or supporting documentation, please
contact our office at {{practice_phone}}. We thank you for your prompt
attention to this matter.

Sincerely,



{{signer_name}}{{signer_credentials_line}}
{{signer_title}}
{{practice_name}}

Enclosures: [BILLER: list as applicable — chart note, EOB, prior auth, etc.]
"""


def get_template_body(template_type: str, level: int) -> str:
    """Return the unrendered body for a (type, level) combination.

    Levels 2 and IRO use the same body but include level-specific framing
    inserted by the renderer.
    """
    base = _BODIES.get(template_type) or _BODIES["general"]
    return base + _BODY_FOOTER


def list_template_types() -> dict:
    return dict(TEMPLATE_TYPES)
