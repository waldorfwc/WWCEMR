"""
Patient Financial Ledger Service.
Produces a complete, chronological financial history for a patient
including all dates of service, charges, insurance payments,
adjustments, patient payments, and running balance.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import asc

from app.models.patient import Patient
from app.models.claim import Claim, ClaimStatus
from app.models.payment import Payment, PaymentType
from app.models.denial import Denial, DenialStatus


# Default ledger window — patient ledgers older than this are excluded
# unless the caller explicitly asks for the full history.
DEFAULT_LEDGER_WINDOW_YEARS = 5


def get_patient_ledger(
    db: Session,
    patient_id: str,
    window_years: Optional[int] = DEFAULT_LEDGER_WINDOW_YEARS,
) -> dict:
    """
    Build a complete financial ledger for a patient.

    window_years: filter claims/payments to the last N years (default 5).
                  Pass 0 or None for full history.
    """
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        return {}

    cutoff: Optional[date] = None
    if window_years and window_years > 0:
        cutoff = date.today() - timedelta(days=365 * window_years)

    claims_q = db.query(Claim).filter(Claim.patient_id == patient_id)
    if cutoff is not None:
        claims_q = claims_q.filter(Claim.date_of_service_from >= cutoff)
    claims = claims_q.order_by(asc(Claim.date_of_service_from)).all()

    payments_q = db.query(Payment).filter(Payment.patient_id == patient_id)
    if cutoff is not None:
        payments_q = payments_q.filter(Payment.payment_date >= cutoff)
    payments = payments_q.order_by(asc(Payment.payment_date)).all()

    # Build DOS-grouped ledger entries
    ledger_by_dos: dict[str, dict] = {}

    for claim in claims:
        dos = str(claim.date_of_service_from or "Unknown")
        if dos not in ledger_by_dos:
            ledger_by_dos[dos] = {
                "date_of_service": dos,
                "claims": [],
                "total_billed": Decimal("0"),
                "total_allowed": Decimal("0"),
                "total_insurance_paid": Decimal("0"),
                "total_contractual": Decimal("0"),
                "total_other_adj": Decimal("0"),
                "total_patient_responsibility": Decimal("0"),
                "total_patient_paid": Decimal("0"),
                "balance": Decimal("0"),
                "denials": [],
            }

        # Claim-level denials
        claim_denials = []
        for denial in claim.denials:
            claim_denials.append({
                "carc_code": denial.carc_code,
                "carc_description": denial.carc_description,
                "rarc_code": denial.rarc_code,
                "denied_amount": float(denial.denied_amount or 0),
                "status": denial.status.value if denial.status else "open",
                "appeal_deadline": str(denial.appeal_deadline) if denial.appeal_deadline else None,
                "category": denial.category.value if denial.category else "other",
                "recommended_action": denial.recommended_action,
                "write_off_recommended": denial.write_off_recommended,
            })

        # Find patient payments applied to this claim
        claim_patient_payments = [
            p for p in payments
            if p.claim_id == claim.id and p.payment_type in (
                PaymentType.PATIENT_PAYMENT, PaymentType.COPAY,
                PaymentType.DEDUCTIBLE, PaymentType.COINSURANCE
            )
        ]
        patient_paid_this_claim = sum(p.amount for p in claim_patient_payments)

        # Allowed amount: explicit value if set (from ERA), else derived from
        # billed - contractual_adjustment (the standard "what insurance allows"
        # definition). For Charge-Analysis-sourced claims allowed_amount is
        # never populated, so derivation is the fallback that always works.
        explicit_allowed = float(claim.allowed_amount or 0)
        derived_allowed = float((claim.billed_amount or Decimal(0)) - (claim.contractual_adjustment or Decimal(0)))
        allowed_for_claim = explicit_allowed if explicit_allowed > 0 else derived_allowed

        claim_entry = {
            "claim_id": str(claim.id),
            "claim_number": claim.claim_number,
            "payer_name": claim.payer_name,
            "insurance_order": claim.insurance_order.value if claim.insurance_order else "primary",
            "status": claim.status.value if claim.status else "pending",
            "billed_amount": float(claim.billed_amount or 0),
            "allowed_amount": allowed_for_claim,
            "paid_amount": float(claim.paid_amount or 0),
            "contractual_adjustment": float(claim.contractual_adjustment or 0),
            "other_adjustment": float(claim.other_adjustment or 0),
            "patient_responsibility": float(claim.patient_responsibility or 0),
            "patient_paid": float(patient_paid_this_claim or 0),
            "balance": float(claim.balance or 0),
            "check_number": claim.check_number,
            "check_date": str(claim.check_date) if claim.check_date else None,
            "denials": claim_denials,
            "service_lines": [
                {
                    "procedure_code": svc.procedure_code,
                    "modifier_1": svc.modifier_1,
                    "description": svc.description,
                    "units": float(svc.units or 1),
                    "billed_amount": float(svc.billed_amount or 0),
                    "paid_amount": float(svc.paid_amount or 0),
                    "patient_responsibility": float(svc.patient_responsibility or 0),
                }
                for svc in claim.service_lines
            ],
        }

        ledger_by_dos[dos]["claims"].append(claim_entry)
        ledger_by_dos[dos]["total_billed"] += claim.billed_amount or 0
        ledger_by_dos[dos]["total_allowed"] += Decimal(str(allowed_for_claim))
        ledger_by_dos[dos]["total_insurance_paid"] += claim.paid_amount or 0
        ledger_by_dos[dos]["total_contractual"] += claim.contractual_adjustment or 0
        ledger_by_dos[dos]["total_other_adj"] += claim.other_adjustment or 0
        ledger_by_dos[dos]["total_patient_responsibility"] += claim.patient_responsibility or 0
        ledger_by_dos[dos]["total_patient_paid"] += patient_paid_this_claim
        ledger_by_dos[dos]["denials"].extend(claim_denials)

    # Calculate DOS-level balances
    for dos_key, entry in ledger_by_dos.items():
        entry["balance"] = float(
            entry["total_patient_responsibility"] - entry["total_patient_paid"]
        )
        for k in ["total_billed", "total_allowed", "total_insurance_paid",
                  "total_contractual", "total_other_adj", "total_patient_responsibility",
                  "total_patient_paid"]:
            entry[k] = float(entry[k])

    # Patient-level payment history (not tied to a specific claim)
    unallocated_payments = [
        p for p in payments if p.claim_id is None
    ]
    payment_history = [
        {
            "date": str(p.payment_date),
            "type": p.payment_type.value if p.payment_type else "",
            "amount": float(p.amount or 0),
            "method": p.payment_method,
            "receipt": p.receipt_number,
            "notes": p.notes,
            "payer": p.payer_name,
            "check_number": p.check_number,
        }
        for p in payments
    ]

    # Grand totals
    total_billed = sum(float(c.billed_amount or 0) for c in claims)
    total_insurance_paid = sum(float(c.paid_amount or 0) for c in claims)
    total_contractual = sum(float(c.contractual_adjustment or 0) for c in claims)
    # Allowed = explicit value when set, else billed - contractual
    total_allowed = sum(
        float(c.allowed_amount or 0) if (c.allowed_amount or 0) > 0
        else float((c.billed_amount or 0) - (c.contractual_adjustment or 0))
        for c in claims
    )
    total_patient_resp = sum(float(c.patient_responsibility or 0) for c in claims)
    total_patient_paid = sum(
        float(p.amount or 0) for p in payments
        if p.payment_type in (PaymentType.PATIENT_PAYMENT, PaymentType.COPAY,
                               PaymentType.DEDUCTIBLE, PaymentType.COINSURANCE)
    )
    total_balance = total_patient_resp - total_patient_paid

    # Denial summary
    all_denials = []
    for claim in claims:
        for denial in claim.denials:
            if denial.status == DenialStatus.OPEN:
                all_denials.append({
                    "denial_id": str(denial.id),
                    "claim_id": str(denial.claim_id),
                    "claim_number": claim.claim_number,
                    "dos": str(claim.date_of_service_from),
                    "payer": claim.payer_name,
                    "carc_code": denial.carc_code,
                    "carc_description": denial.carc_description,
                    "denied_amount": float(denial.denied_amount or 0),
                    "appeal_deadline": str(denial.appeal_deadline) if denial.appeal_deadline else None,
                    "category": denial.category.value if denial.category else "other",
                    "recommended_action": denial.recommended_action,
                })

    return {
        "patient": {
            "id": str(patient.id),
            "patient_id": patient.patient_id,
            "full_name": patient.full_name,
            "date_of_birth": str(patient.date_of_birth) if patient.date_of_birth else None,
            "address": patient.address,
            "phone": patient.phone,
            "email": patient.email,
            "primary_insurance": patient.primary_insurance_name,
            "secondary_insurance": patient.secondary_insurance_name,
            "tertiary_insurance": patient.tertiary_insurance_name,
            # Placeholder for the four credit fields that come from Charge
            # Analysis ingest. Populated as zero until that's wired up.
            "credits": {
                "insurance": 0, "patient": 0, "pre_pay": 0, "undetermined": 0,
            },
        },
        "summary": {
            "total_billed": total_billed,
            "total_allowed": total_allowed,
            "total_insurance_paid": total_insurance_paid,
            "total_contractual_adjustment": total_contractual,
            "total_patient_responsibility": total_patient_resp,
            "total_patient_paid": total_patient_paid,
            "outstanding_balance": total_balance,
            "claim_count": len(claims),
            "open_denial_count": len(all_denials),
            "open_denial_amount": sum(d["denied_amount"] for d in all_denials),
        },
        "dos_entries": sorted(ledger_by_dos.values(), key=lambda x: x["date_of_service"]),
        "payment_history": sorted(payment_history, key=lambda x: x["date"]),
        "open_denials": all_denials,
        "generated_at": str(date.today()),
    }
