"""
CARC (Claim Adjustment Reason Codes) and RARC (Remittance Advice Remark Codes)
with denial categorization and recommended actions.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class DenialCategory(str, Enum):
    TIMELY_FILING = "timely_filing"
    AUTHORIZATION = "authorization"
    MEDICAL_NECESSITY = "medical_necessity"
    ELIGIBILITY = "eligibility"
    DUPLICATE = "duplicate"
    CODING = "coding"
    COB = "cob"
    PROVIDER_CREDENTIALING = "provider_credentialing"
    MISSING_INFORMATION = "missing_information"
    BENEFIT_LIMIT = "benefit_limit"
    NON_COVERED = "non_covered"
    CONTRACTUAL = "contractual"   # CO-45 etc — typically write-off
    OTHER = "other"


@dataclass
class CarcInfo:
    code: str
    description: str
    category: DenialCategory
    appealable: bool = True
    write_off_recommended: bool = False
    recommended_action: str = "appeal"
    action_notes: str = ""


# Key CARC codes — comprehensive list of commonly seen codes
CARC_CODES: dict[str, CarcInfo] = {
    "1": CarcInfo("1", "Deductible Amount", DenialCategory.ELIGIBILITY, True, False, "bill_patient", "Bill patient for deductible"),
    "2": CarcInfo("2", "Coinsurance Amount", DenialCategory.ELIGIBILITY, True, False, "bill_patient", "Bill patient for coinsurance"),
    "3": CarcInfo("3", "Co-payment Amount", DenialCategory.ELIGIBILITY, True, False, "bill_patient", "Bill patient for copay"),
    "4": CarcInfo("4", "The service is inconsistent with the modifier", DenialCategory.CODING, True, False, "correct_and_resubmit", "Review modifier usage and resubmit with correct modifier"),
    "5": CarcInfo("5", "The procedure code is inconsistent with the modifier used", DenialCategory.CODING, True, False, "correct_and_resubmit"),
    "6": CarcInfo("6", "The procedure/revenue code is inconsistent with the patient's age", DenialCategory.CODING, True, False, "correct_and_resubmit"),
    "7": CarcInfo("7", "The procedure/revenue code is inconsistent with the patient's gender", DenialCategory.CODING, True, False, "correct_and_resubmit"),
    "8": CarcInfo("8", "The procedure code is inconsistent with the provider type/specialty", DenialCategory.CODING, True, False, "appeal"),
    "9": CarcInfo("9", "The diagnosis is inconsistent with the patient's age", DenialCategory.CODING, True, False, "correct_and_resubmit"),
    "10": CarcInfo("10", "The diagnosis is inconsistent with the patient's gender", DenialCategory.CODING, True, False, "correct_and_resubmit"),
    "11": CarcInfo("11", "The diagnosis is inconsistent with the procedure", DenialCategory.CODING, True, False, "correct_and_resubmit", "Verify ICD-10 supports procedure and resubmit"),
    "13": CarcInfo("13", "The date of death precedes the date of service", DenialCategory.ELIGIBILITY, False, True, "write_off", "Patient deceased before DOS — verify and write off"),
    "15": CarcInfo("15", "The authorization number is missing, invalid, or does not apply", DenialCategory.AUTHORIZATION, True, False, "appeal", "Obtain retro authorization or appeal with medical necessity documentation"),
    "16": CarcInfo("16", "Claim/service lacks information or has submission/billing error", DenialCategory.MISSING_INFORMATION, True, False, "correct_and_resubmit", "Review claim for missing fields and resubmit"),
    "18": CarcInfo("18", "Exact duplicate claim/service", DenialCategory.DUPLICATE, False, True, "write_off", "Confirm original claim paid; if not, appeal as non-duplicate"),
    "19": CarcInfo("19", "Claim denied because this is a work-related injury", DenialCategory.OTHER, True, False, "redirect", "Bill Workers' Compensation carrier"),
    "20": CarcInfo("20", "Claim denied because this injury/illness is covered by the liability carrier", DenialCategory.OTHER, True, False, "redirect"),
    "21": CarcInfo("21", "Claim denied because this injury/illness is the liability of a third party", DenialCategory.COB, True, False, "redirect"),
    "22": CarcInfo("22", "This care may be covered by another payer per coordination of benefits", DenialCategory.COB, True, False, "submit_to_primary", "Submit to primary insurance first"),
    "23": CarcInfo("23", "The impact of prior payer(s) adjudication including payments and/or adjustments", DenialCategory.COB, False, False, "informational"),
    "24": CarcInfo("24", "Charges are covered under a capitation agreement", DenialCategory.NON_COVERED, False, True, "write_off"),
    "26": CarcInfo("26", "Expenses incurred prior to coverage", DenialCategory.ELIGIBILITY, True, False, "verify_eligibility", "Verify DOS vs effective date; appeal if coverage active"),
    "27": CarcInfo("27", "Expenses incurred after coverage terminated", DenialCategory.ELIGIBILITY, True, False, "verify_eligibility", "Verify termination date; appeal if coverage active on DOS"),
    "29": CarcInfo("29", "The time limit for filing has expired", DenialCategory.TIMELY_FILING, True, False, "appeal", "Gather proof of timely filing (clearinghouse reports, delivery receipts) and appeal"),
    "31": CarcInfo("31", "Claim denied as patient cannot be identified as our insured", DenialCategory.ELIGIBILITY, True, False, "verify_eligibility", "Verify member ID, DOB, name; resubmit or appeal with enrollment proof"),
    "32": CarcInfo("32", "Our records indicate that this dependent is not an eligible dependent", DenialCategory.ELIGIBILITY, True, False, "appeal"),
    "33": CarcInfo("33", "Insured has no dependent coverage", DenialCategory.ELIGIBILITY, True, False, "appeal"),
    "34": CarcInfo("34", "Insured has no coverage for newborns", DenialCategory.ELIGIBILITY, True, False, "appeal"),
    "35": CarcInfo("35", "Lifetime benefit maximum has been reached", DenialCategory.BENEFIT_LIMIT, True, False, "appeal"),
    "36": CarcInfo("36", "Balance does not exceed co-payment amount", DenialCategory.ELIGIBILITY, False, True, "write_off"),
    "39": CarcInfo("39", "Services denied at the time authorization/pre-certification was requested", DenialCategory.AUTHORIZATION, True, False, "appeal"),
    "40": CarcInfo("40", "Charges do not meet qualifications for emergent/urgent care", DenialCategory.MEDICAL_NECESSITY, True, False, "appeal"),
    "44": CarcInfo("44", "Prompt-pay discount", DenialCategory.CONTRACTUAL, False, False, "informational"),
    "45": CarcInfo("45", "Charge exceeds fee schedule/maximum allowable amount", DenialCategory.CONTRACTUAL, False, True, "write_off", "CO-45 is a contractual write-off — do NOT bill patient"),
    "49": CarcInfo("49", "This is a non-covered service because it is a routine/preventive exam", DenialCategory.NON_COVERED, True, False, "appeal"),
    "50": CarcInfo("50", "These are non-covered services because this is not deemed a medical necessity", DenialCategory.MEDICAL_NECESSITY, True, False, "appeal", "Appeal with clinical notes, treatment plan, and medical necessity letter"),
    "51": CarcInfo("51", "These are non-covered services because this is a pre-existing condition", DenialCategory.NON_COVERED, True, False, "appeal"),
    "55": CarcInfo("55", "Claim/service denied because procedure/treatment is deemed experimental", DenialCategory.MEDICAL_NECESSITY, True, False, "appeal"),
    "57": CarcInfo("57", "Payment denied/reduced because the payer deems the information submitted does not support this level of service", DenialCategory.MEDICAL_NECESSITY, True, False, "appeal"),
    "58": CarcInfo("58", "Treatment was deemed by the payer to have been rendered in an inappropriate or invalid place of service", DenialCategory.CODING, True, False, "appeal"),
    "59": CarcInfo("59", "Processed based on multiple or concurrent procedure rules", DenialCategory.CODING, True, False, "appeal"),
    "60": CarcInfo("60", "Charges for outpatient services are not covered when performed within a period of time prior to or after inpatient services", DenialCategory.CODING, True, False, "appeal"),
    "76": CarcInfo("76", "Duplicate of a claim processed/being processed by another payer", DenialCategory.DUPLICATE, True, False, "appeal"),
    "97": CarcInfo("97", "The benefit for this service is included in the payment/allowance for another service/procedure that has already been adjudicated", DenialCategory.CODING, True, False, "appeal", "Unbundling — appeal with documentation that service is separately payable"),
    "109": CarcInfo("109", "Claim/service not covered by this payer/contractor. You must send the claim/service to the correct payer/contractor", DenialCategory.ELIGIBILITY, True, False, "redirect", "Verify payer and resubmit to correct payer"),
    "119": CarcInfo("119", "Benefit maximum for this time period or occurrence has been reached", DenialCategory.BENEFIT_LIMIT, True, False, "appeal"),
    "120": CarcInfo("120", "Patient is covered by a managed care plan", DenialCategory.COB, True, False, "redirect"),
    "125": CarcInfo("125", "Submission/billing error(s)", DenialCategory.MISSING_INFORMATION, True, False, "correct_and_resubmit"),
    "129": CarcInfo("129", "Prior processing information appears incorrect", DenialCategory.CODING, True, False, "appeal"),
    "133": CarcInfo("133", "The disposition of this claim/service is pending further review", DenialCategory.OTHER, False, False, "wait"),
    "136": CarcInfo("136", "Failure to follow prior payer's coverage limitations", DenialCategory.COB, True, False, "appeal"),
    "140": CarcInfo("140", "Patient/Insured health identification number and name do not match", DenialCategory.ELIGIBILITY, True, False, "correct_and_resubmit"),
    "146": CarcInfo("146", "Diagnosis was invalid for the date(s) of service reported", DenialCategory.CODING, True, False, "correct_and_resubmit"),
    "150": CarcInfo("150", "Payer deems the information submitted does not support this level of service", DenialCategory.MEDICAL_NECESSITY, True, False, "appeal"),
    "151": CarcInfo("151", "Payment adjusted because the payer deems the information submitted does not support this many/frequency of services", DenialCategory.MEDICAL_NECESSITY, True, False, "appeal"),
    "152": CarcInfo("152", "Payer deems the information submitted does not support this length of service", DenialCategory.MEDICAL_NECESSITY, True, False, "appeal"),
    "166": CarcInfo("166", "These services were submitted after this plan's filing limit", DenialCategory.TIMELY_FILING, True, False, "appeal"),
    "167": CarcInfo("167", "This (these) diagnosis(es) is (are) not covered", DenialCategory.NON_COVERED, True, False, "appeal"),
    "170": CarcInfo("170", "Payment is denied when performed/billed by this type of provider in this type of facility", DenialCategory.PROVIDER_CREDENTIALING, True, False, "appeal"),
    "171": CarcInfo("171", "Payment is denied for services provided by a provider who was not credentialed", DenialCategory.PROVIDER_CREDENTIALING, True, False, "appeal"),
    "173": CarcInfo("173", "No authorization obtained for this service/item", DenialCategory.AUTHORIZATION, True, False, "appeal"),
    "175": CarcInfo("175", "Claim/service denied because authorization/referral was not obtained prior to rendering of the service", DenialCategory.AUTHORIZATION, True, False, "appeal", "Request retro authorization; appeal with medical necessity if denied"),
    "181": CarcInfo("181", "Procedure code was invalid on the date of service", DenialCategory.CODING, True, False, "correct_and_resubmit"),
    "182": CarcInfo("182", "Procedure modifier was invalid on the date of service", DenialCategory.CODING, True, False, "correct_and_resubmit"),
    "183": CarcInfo("183", "The referring provider is not eligible to refer the service billed", DenialCategory.PROVIDER_CREDENTIALING, True, False, "appeal"),
    "185": CarcInfo("185", "The rendering provider is not eligible to perform the service billed", DenialCategory.PROVIDER_CREDENTIALING, True, False, "appeal"),
    "197": CarcInfo("197", "Precertification/authorization/notification absent", DenialCategory.AUTHORIZATION, True, False, "appeal", "Request retro auth or appeal with medical records"),
    "204": CarcInfo("204", "This service/equipment/drug is not covered under the patient's current benefit plan", DenialCategory.NON_COVERED, True, False, "appeal"),
    "222": CarcInfo("222", "Exceeds the contracted maximum number of hours/days/units", DenialCategory.BENEFIT_LIMIT, True, False, "appeal"),
    "226": CarcInfo("226", "Information requested from the patient/insured/responsible party was not provided or was insufficient/incomplete", DenialCategory.MISSING_INFORMATION, True, False, "appeal"),
    "227": CarcInfo("227", "Information requested from the provider was not provided or was insufficient/incomplete", DenialCategory.MISSING_INFORMATION, True, False, "appeal"),
    "234": CarcInfo("234", "This procedure is not paid separately", DenialCategory.CODING, True, False, "appeal"),
    "242": CarcInfo("242", "Services not provided by a network/primary care providers", DenialCategory.PROVIDER_CREDENTIALING, True, False, "appeal"),
    "243": CarcInfo("243", "Services not authorized by network/primary care providers", DenialCategory.AUTHORIZATION, True, False, "appeal"),
    "252": CarcInfo("252", "An attachment/other documentation is required to adjudicate this claim/service", DenialCategory.MISSING_INFORMATION, True, False, "submit_documentation"),
    "253": CarcInfo("253", "Sequencing Error", DenialCategory.CODING, True, False, "correct_and_resubmit"),
    "256": CarcInfo("256", "Service not payable per managed care contract", DenialCategory.NON_COVERED, True, False, "appeal"),
    "272": CarcInfo("272", "Coverage/program guidelines were not met", DenialCategory.MEDICAL_NECESSITY, True, False, "appeal"),
    "273": CarcInfo("273", "Coverage/program guidelines were exceeded", DenialCategory.BENEFIT_LIMIT, True, False, "appeal"),
    "274": CarcInfo("274", "Fee/Service not payable per managed care contract", DenialCategory.NON_COVERED, False, True, "write_off"),
    "275": CarcInfo("275", "Prior payer's (or payers') patient responsibility not covered", DenialCategory.COB, True, False, "appeal"),
}


# Key RARC codes
RARC_CODES: dict[str, str] = {
    "M1": "X-ray not taken within the past 12 months or near the time of treatment",
    "M15": "Separately billed services/tests have been bundled",
    "M16": "Alert: Please review your record and resubmit if appropriate",
    "M20": "Missing/incomplete/invalid HCPCS",
    "M25": "Missing/incomplete/invalid information",
    "M51": "Missing/incomplete/invalid procedure code(s)",
    "M55": "Not covered; here are your Medicare appeal rights",
    "M62": "Missing/incomplete/invalid treatment authorization code",
    "M76": "Missing/incomplete/invalid diagnosis or condition",
    "M77": "Missing/incomplete/invalid place of service",
    "M80": "Not covered when performed during the same session/date as a previously processed service for the patient",
    "M81": "You have filed this claim electronically; the electronic version of this remittance advice is the official record",
    "M86": "Service denied because payment already made for same/similar procedure within set time frame",
    "M87": "Claim/service(s) subjected to review",
    "MA01": "Alert: If you do not agree with what we approved for these services, you may appeal our decision",
    "MA04": "Secondary payment cannot be considered without the identity of or payment information from the primary payer",
    "MA07": "Alert: The claim information is also being forwarded to the patient's supplemental insurer",
    "MA08": "Alert: Incomplete/invalid health insurance claim number",
    "MA18": "Alert: The claim information is also being forwarded to the patient's supplemental insurer",
    "MA130": "Your claim contains incomplete and/or invalid information, and no appeal rights are afforded",
    "N1": "Alert: You may appeal this decision",
    "N3": "Missing/incomplete/invalid prior authorization or referral number",
    "N4": "Missing/incomplete/invalid entry in the primary payer payment amount field",
    "N17": "Claim requires mammography certification",
    "N19": "Procedure code incidental to primary procedure",
    "N20": "Service not payable with other service rendered on the same date",
    "N24": "Missing/incomplete/invalid electronic funds transfer (EFT) banking information",
    "N30": "Patient ineligible for this service",
    "N31": "Missing/incomplete/invalid prescribing provider primary identifier",
    "N45": "Payment based on authorized amount",
    "N57": "Missing/incomplete/invalid other insurance information",
    "N58": "Missing/incomplete/invalid other insurance payment information",
    "N115": "This decision was based on a Local Coverage Determination (LCD)",
    "N130": "Consult plan benefit documents/guidelines for information about restrictions for this service",
    "N180": "This service is only covered when the patient has a confirmed diagnosis of a specific condition",
    "N264": "Missing/incomplete/invalid ordering provider name",
    "N265": "Missing/incomplete/invalid ordering provider primary identifier",
    "N270": "Missing/incomplete/invalid rendering provider primary identifier",
    "N272": "Missing/incomplete/invalid rendering provider address",
    "N522": "Duplicate of a claim processed or being processed by another payer",
    "N575": "Mismatch between the submitted ordering/referring provider name and the ordering/referring provider name stored in our system",
    "N655": "Alert: Rebilling this claim may result in lower reimbursement",
    "N781": "This claim has been identified as a duplicate",
    "N822": "Missing/incomplete/invalid procedure modifiers",
    "N823": "Incomplete/invalid modifier(s)",
    "N826": "Self-referral not permitted",
    "N862": "Alert: Contracted providers must submit a corrected claim directly to the primary payer before submitting to the secondary payer",
    "PR": "Patient Responsibility",
    "OA1": "Non-covered charge",
}


def get_carc_info(code: str) -> CarcInfo:
    info = CARC_CODES.get(str(code))
    if info:
        return info
    return CarcInfo(
        code=str(code),
        description=f"Adjustment reason code {code}",
        category=DenialCategory.OTHER,
        appealable=True,
        write_off_recommended=False,
        recommended_action="research",
        action_notes="Research this adjustment code for appropriate action",
    )


def get_rarc_description(code: str) -> str:
    return RARC_CODES.get(code, f"Remark code {code}")
