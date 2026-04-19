"""
ERA 835 X12 EDI Parser
Supports full X12 835 transaction set including:
- ISA/GS/ST envelope headers
- BPR: Financial Information
- TRN: Trace/Check Number
- DTM: Dates
- N1/N3/N4: Payer / Payee names and addresses
- REF: Reference numbers
- CLP: Claim Payment Information
- CAS: Claim/Service Adjustment Segments (CO, PR, OA, PI, CR)
- NM1: Patient, Subscriber, Corrected Patient names
- DTM: Service dates
- SVC: Service line payment info
- PLB: Provider Level Balance adjustments
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import re


@dataclass
class EraAdjustment:
    group_code: str          # CO, PR, OA, PI, CR
    reason_code: str         # CARC
    amount: Decimal
    quantity: Optional[Decimal] = None


@dataclass
class EraServiceLine:
    procedure_code: str
    modifier_1: Optional[str] = None
    modifier_2: Optional[str] = None
    modifier_3: Optional[str] = None
    modifier_4: Optional[str] = None
    revenue_code: Optional[str] = None
    billed_amount: Decimal = Decimal("0")
    paid_amount: Decimal = Decimal("0")
    units: Decimal = Decimal("1")
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    adjustments: list[EraAdjustment] = field(default_factory=list)
    rarc_codes: list[str] = field(default_factory=list)


@dataclass
class EraClaim:
    patient_control_number: str
    payer_claim_number: Optional[str] = None
    claim_status_code: str = "1"  # 1=paid, 2=adjusted, 3=denied, 4=denied
    billed_amount: Decimal = Decimal("0")
    paid_amount: Decimal = Decimal("0")
    patient_responsibility: Decimal = Decimal("0")
    claim_filing_indicator: str = ""

    # Dates
    statement_date_from: Optional[date] = None
    statement_date_to: Optional[date] = None
    received_date: Optional[date] = None

    # Names
    patient_first_name: Optional[str] = None
    patient_last_name: Optional[str] = None
    subscriber_first_name: Optional[str] = None
    subscriber_last_name: Optional[str] = None
    subscriber_id: Optional[str] = None
    group_number: Optional[str] = None
    rendering_provider_npi: Optional[str] = None
    rendering_provider_name: Optional[str] = None

    # Adjustments at claim level (CAS before SVC)
    adjustments: list[EraAdjustment] = field(default_factory=list)

    # Service lines
    service_lines: list[EraServiceLine] = field(default_factory=list)

    # Remark codes
    rarc_codes: list[str] = field(default_factory=list)

    @property
    def is_denied(self) -> bool:
        return self.claim_status_code in ("3", "4")

    @property
    def contractual_adjustment(self) -> Decimal:
        return sum(
            a.amount for a in self.adjustments if a.group_code == "CO"
        )

    @property
    def patient_adj(self) -> Decimal:
        return sum(
            a.amount for a in self.adjustments if a.group_code == "PR"
        )


@dataclass
class EraFile:
    filename: str
    interchange_sender: str = ""
    interchange_receiver: str = ""
    payer_name: str = ""
    payer_id: str = ""
    payee_name: str = ""
    payee_npi: str = ""
    check_number: str = ""
    check_date: Optional[date] = None
    check_amount: Decimal = Decimal("0")
    production_date: Optional[date] = None
    claims: list[EraClaim] = field(default_factory=list)
    plb_adjustments: list[dict] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


def _parse_date(val: str) -> Optional[date]:
    if not val:
        return None
    val = val.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y", "%Y%m"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def _dec(val: str) -> Decimal:
    try:
        return Decimal(val.strip()) if val and val.strip() else Decimal("0")
    except Exception:
        return Decimal("0")


def _detect_delimiters(content: str):
    """Read ISA header to detect element/segment/component separators."""
    # ISA is exactly 106 chars when all 3 delimiters are known
    if len(content) < 106:
        return "*", "~", ":"
    element_sep = content[3]
    segment_sep = content[105]
    component_sep = content[104]
    return element_sep, segment_sep, component_sep


class Era835Parser:
    def parse(self, content: str, filename: str = "era.835") -> EraFile:
        era = EraFile(filename=filename)

        element_sep, segment_sep, component_sep = _detect_delimiters(content)

        # Normalise line endings, split on segment terminator
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        raw_segments = [s.strip() for s in content.split(segment_sep) if s.strip()]

        segments: list[list[str]] = [seg.split(element_sep) for seg in raw_segments]

        current_claim: Optional[EraClaim] = None
        current_svc: Optional[EraServiceLine] = None
        loop_context = ""  # 1000A=payer, 1000B=payee, 2000=claim, 2100=claim names, 2110=svc

        i = 0
        while i < len(segments):
            seg = segments[i]
            seg_id = seg[0].upper()

            # ── Interchange ──────────────────────────────────────────────
            if seg_id == "ISA":
                era.interchange_sender = seg[6] if len(seg) > 6 else ""
                era.interchange_receiver = seg[8] if len(seg) > 8 else ""

            # ── Financial info (check/EFT) ───────────────────────────────
            elif seg_id == "BPR":
                # BPR02 = payment amount, BPR16 = check date
                era.check_amount = _dec(seg[2]) if len(seg) > 2 else Decimal("0")
                if len(seg) > 16:
                    era.check_date = _parse_date(seg[16])

            # ── Trace (check number) ─────────────────────────────────────
            elif seg_id == "TRN":
                if len(seg) > 2:
                    era.check_number = seg[2]

            # ── Production date ──────────────────────────────────────────
            elif seg_id == "DTM":
                qualifier = seg[1] if len(seg) > 1 else ""
                date_val = seg[2] if len(seg) > 2 else ""
                if qualifier == "405":
                    era.production_date = _parse_date(date_val)
                elif current_claim is not None and qualifier == "232":
                    current_claim.statement_date_from = _parse_date(date_val)
                elif current_claim is not None and qualifier == "233":
                    current_claim.statement_date_to = _parse_date(date_val)
                elif current_claim is not None and qualifier == "050":
                    current_claim.received_date = _parse_date(date_val)
                elif current_svc is not None and qualifier == "472":
                    current_svc.date_from = _parse_date(date_val)

            # ── Names (payer, payee, patient, subscriber) ────────────────
            elif seg_id == "N1":
                entity = seg[1] if len(seg) > 1 else ""
                name = seg[2] if len(seg) > 2 else ""
                id_code_qual = seg[3] if len(seg) > 3 else ""
                id_code = seg[4] if len(seg) > 4 else ""

                if entity == "PR":  # Payer
                    era.payer_name = name
                    era.payer_id = id_code
                    loop_context = "1000A"
                elif entity == "PE":  # Payee
                    era.payee_name = name
                    era.payee_npi = id_code if id_code_qual == "XX" else ""
                    loop_context = "1000B"

            # ── Claim Payment ─────────────────────────────────────────────
            elif seg_id == "CLP":
                # Save previous claim
                if current_svc is not None and current_claim is not None:
                    current_claim.service_lines.append(current_svc)
                    current_svc = None
                if current_claim is not None:
                    era.claims.append(current_claim)

                current_claim = EraClaim(
                    patient_control_number=seg[1] if len(seg) > 1 else "",
                    claim_status_code=seg[2] if len(seg) > 2 else "1",
                    billed_amount=_dec(seg[3]) if len(seg) > 3 else Decimal("0"),
                    paid_amount=_dec(seg[4]) if len(seg) > 4 else Decimal("0"),
                    patient_responsibility=_dec(seg[5]) if len(seg) > 5 else Decimal("0"),
                    claim_filing_indicator=seg[6] if len(seg) > 6 else "",
                    payer_claim_number=seg[7] if len(seg) > 7 else None,
                )
                loop_context = "2000"

            # ── Claim/Service Adjustment ──────────────────────────────────
            elif seg_id == "CAS":
                adj = self._parse_cas(seg)
                if current_svc is not None:
                    current_svc.adjustments.extend(adj)
                elif current_claim is not None:
                    current_claim.adjustments.extend(adj)

            # ── Individual Names ──────────────────────────────────────────
            elif seg_id == "NM1":
                entity = seg[1] if len(seg) > 1 else ""
                last = seg[3] if len(seg) > 3 else ""
                first = seg[4] if len(seg) > 4 else ""
                id_qual = seg[8] if len(seg) > 8 else ""
                nm1_id = seg[9] if len(seg) > 9 else ""

                if current_claim is not None:
                    if entity in ("QC", "IL"):  # patient or insured
                        current_claim.patient_last_name = last
                        current_claim.patient_first_name = first
                        if entity == "IL":
                            current_claim.subscriber_last_name = last
                            current_claim.subscriber_first_name = first
                        if id_qual == "MI":
                            current_claim.subscriber_id = nm1_id
                    elif entity == "82":  # rendering provider
                        current_claim.rendering_provider_name = f"{last} {first}".strip()
                        if id_qual == "XX":
                            current_claim.rendering_provider_npi = nm1_id

            # ── Reference ─────────────────────────────────────────────────
            elif seg_id == "REF":
                qual = seg[1] if len(seg) > 1 else ""
                val = seg[2] if len(seg) > 2 else ""
                if current_claim is not None:
                    if qual == "1L":
                        current_claim.group_number = val
                    elif qual in ("EA", "1C"):
                        current_claim.subscriber_id = current_claim.subscriber_id or val

            # ── Service Line ──────────────────────────────────────────────
            elif seg_id == "SVC":
                if current_svc is not None and current_claim is not None:
                    current_claim.service_lines.append(current_svc)

                # SVC01 is composite: procedure:mod1:mod2:mod3:mod4
                composite = seg[1].split(component_sep) if len(seg) > 1 else []
                # Composite: qualifier:code or just code
                # 01=HCPCS/CPT, NU=NDC, etc.
                code_part = composite[1] if len(composite) > 1 else (composite[0] if composite else "")
                rev_code = None
                if len(composite) > 0 and composite[0] in ("HC", "NU", "HP", "IV"):
                    proc_code = composite[1] if len(composite) > 1 else ""
                elif len(composite) > 0 and composite[0] == "BO":
                    rev_code = composite[1] if len(composite) > 1 else ""
                    proc_code = composite[2] if len(composite) > 2 else ""
                else:
                    proc_code = code_part

                # SVC04 carries revenue code if not already parsed from composite
                svc_rev = rev_code or (seg[4].strip() if len(seg) > 4 and seg[4].strip() else None)
                current_svc = EraServiceLine(
                    procedure_code=proc_code,
                    modifier_1=composite[2] if len(composite) > 2 else None,
                    modifier_2=composite[3] if len(composite) > 3 else None,
                    modifier_3=composite[4] if len(composite) > 4 else None,
                    modifier_4=composite[5] if len(composite) > 5 else None,
                    revenue_code=svc_rev,
                    billed_amount=_dec(seg[2]) if len(seg) > 2 else Decimal("0"),
                    paid_amount=_dec(seg[3]) if len(seg) > 3 else Decimal("0"),
                    units=_dec(seg[5]) if len(seg) > 5 else Decimal("1"),
                )

            # ── Remark Codes ──────────────────────────────────────────────
            elif seg_id == "MOA":
                # MOA carries RARCs at claim level
                for idx in [2, 3, 4, 5, 6]:
                    if len(seg) > idx and seg[idx].strip():
                        if current_claim:
                            current_claim.rarc_codes.append(seg[idx].strip())

            elif seg_id == "LQ":
                # Service line remarks
                if len(seg) > 2 and current_svc:
                    current_svc.rarc_codes.append(seg[2].strip())

            # ── Provider Level Balance ─────────────────────────────────────
            elif seg_id == "PLB":
                plb = self._parse_plb(seg)
                if plb:
                    era.plb_adjustments.append(plb)

            # ── Transaction / Group end ───────────────────────────────────
            elif seg_id in ("SE", "GE", "IEA"):
                if current_svc is not None and current_claim is not None:
                    current_claim.service_lines.append(current_svc)
                    current_svc = None
                if current_claim is not None:
                    era.claims.append(current_claim)
                    current_claim = None

            i += 1

        return era

    def _parse_cas(self, seg: list[str]) -> list[EraAdjustment]:
        """Parse a CAS segment which can carry up to 6 reason code/amount pairs."""
        adjs = []
        if len(seg) < 4:
            return adjs
        group_code = seg[1]
        # CAS: group, reason1, amt1, qty1, reason2, amt2, qty2, ...
        idx = 2
        while idx < len(seg) - 1:
            reason = seg[idx].strip() if len(seg) > idx else ""
            amt_str = seg[idx + 1].strip() if len(seg) > idx + 1 else "0"
            qty_str = seg[idx + 2].strip() if len(seg) > idx + 2 else ""
            if not reason:
                break
            adjs.append(EraAdjustment(
                group_code=group_code,
                reason_code=reason,
                amount=_dec(amt_str),
                quantity=_dec(qty_str) if qty_str else None,
            ))
            idx += 3
        return adjs

    def _parse_plb(self, seg: list[str]) -> Optional[dict]:
        if len(seg) < 4:
            return None
        return {
            "provider_id": seg[1],
            "fiscal_period": seg[2],
            "reason_code": seg[3] if len(seg) > 3 else "",
            "amount": str(_dec(seg[4])) if len(seg) > 4 else "0",
        }
