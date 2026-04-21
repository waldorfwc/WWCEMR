"""ERA 835 payment-posting service.

build_preview() is pure (no DB writes) — it classifies each EraClaim into a
status and returns the plan. The commit step in the router does all writes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Literal, Optional

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
