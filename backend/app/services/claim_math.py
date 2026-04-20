"""Computed-field helpers for the Claim model."""
from decimal import Decimal
from app.models.claim import Claim


def recompute_balance(claim: Claim) -> None:
    """Set claim.balance = billed - contractual - other - paid - pt_resp.

    Mutates `claim` in place. Does NOT commit — caller commits the session.
    Handles None fields by treating them as zero.
    """
    claim.balance = (
        (claim.billed_amount or Decimal(0))
        - (claim.contractual_adjustment or Decimal(0))
        - (claim.other_adjustment or Decimal(0))
        - (claim.paid_amount or Decimal(0))
        - (claim.patient_responsibility or Decimal(0))
    )
