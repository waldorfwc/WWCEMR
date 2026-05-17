"""Match a Surgery to its consent templates.

For each procedure on a surgery, we find the *one* primary
ConsentTemplate whose `procedure_match` keywords (substring,
case-insensitive) cover that procedure name. Then we add any
supplemental templates whose procedure + insurance + facility match.

Supplemental example: Medicaid HHS-687 sterilization consent attaches to
any tubal/sterilization procedure when the patient has one of the listed
Medicaid MCO insurances (Priority Partners, Maryland Physicians Care,
United Healthcare Community Plan, Wellpoint, Blue Cross Family Plan,
MedStar Family Plan).

Each match comes back with an optional warning (e.g. Medicaid 30-day
rule violation), which the caller can choose to surface or block on.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as _date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.surgery import ConsentTemplate, Surgery


@dataclass
class TemplateMatch:
    template: ConsentTemplate
    matched_procedure: Optional[str]   # which surgery procedure triggered this match (None for supplemental)
    is_supplemental: bool
    warning: Optional[str] = None


def _lc(s) -> str:
    return (s or "").lower()


def _procs_list(s: Surgery) -> list[str]:
    procs = s.procedures or []
    if isinstance(procs, str):
        try:
            procs = json.loads(procs)
        except Exception:
            procs = [procs]
    return [str(p) for p in procs if p]


def _procedure_template_matches(t: ConsentTemplate, procedure_text: str) -> bool:
    if not t.procedure_match:
        return False
    needle = procedure_text.lower()
    return any(kw and kw.lower() in needle for kw in t.procedure_match)


def _facility_template_matches(t: ConsentTemplate, surgery_facility: Optional[str]) -> bool:
    if not t.facility_match:
        return True
    return _lc(t.facility_match) == _lc(surgery_facility)


def _insurance_template_matches(t: ConsentTemplate, primary_insurance: Optional[str]) -> bool:
    if not t.insurance_match:
        return True
    if not primary_insurance:
        return False
    needle = primary_insurance.lower()
    return any(kw and kw.lower() in needle for kw in t.insurance_match)


def _check_min_days_warning(t: ConsentTemplate, scheduled_date: Optional[_date],
                             today: Optional[_date] = None) -> Optional[str]:
    if t.min_days_before_surgery is None or not scheduled_date:
        return None
    today = today or _date.today()
    days_until = (scheduled_date - today).days
    if days_until < t.min_days_before_surgery:
        return (f"{t.name}: must be signed at least "
                f"{t.min_days_before_surgery} days before surgery "
                f"(only {days_until} days remain).")
    return None


def match_templates_for_surgery(db: Session, surgery: Surgery,
                                  today: Optional[_date] = None) -> list[TemplateMatch]:
    """Return the ordered list of template matches for this surgery.

    Order: one primary template per procedure (in surgery.procedures order),
    followed by any supplemental matches. Empty list when no template
    matches — caller should refuse to send.
    """
    all_active = (db.query(ConsentTemplate)
                    .filter(ConsentTemplate.is_active.is_(True))
                    .all())
    primaries = [t for t in all_active if not t.is_supplemental]
    supplementals = [t for t in all_active if t.is_supplemental]

    matches: list[TemplateMatch] = []
    matched_template_ids: set = set()

    surgery_facility = surgery.selected_facility
    primary_insurance = surgery.primary_insurance

    # Primary: one template per procedure on the surgery
    for proc in _procs_list(surgery):
        for t in primaries:
            if (_procedure_template_matches(t, proc)
                    and _facility_template_matches(t, surgery_facility)
                    and _insurance_template_matches(t, primary_insurance)
                    and t.id not in matched_template_ids):
                matches.append(TemplateMatch(
                    template=t,
                    matched_procedure=proc,
                    is_supplemental=False,
                    warning=_check_min_days_warning(t, surgery.scheduled_date, today),
                ))
                matched_template_ids.add(t.id)
                break  # one primary per procedure

    # Supplemental: attach when procedure + insurance + facility all match
    for t in supplementals:
        if t.id in matched_template_ids:
            continue
        if not _facility_template_matches(t, surgery_facility):
            continue
        if not _insurance_template_matches(t, primary_insurance):
            continue
        # Supplemental must also reference at least one of the surgery's procedures
        triggering_proc = None
        for proc in _procs_list(surgery):
            if _procedure_template_matches(t, proc):
                triggering_proc = proc
                break
        if not triggering_proc:
            continue
        matches.append(TemplateMatch(
            template=t,
            matched_procedure=triggering_proc,
            is_supplemental=True,
            warning=_check_min_days_warning(t, surgery.scheduled_date, today),
        ))
        matched_template_ids.add(t.id)

    return matches


def unmatched_procedures(db: Session, surgery: Surgery) -> list[str]:
    """List procedures on the surgery that no primary template covers.
    Useful for telling staff what they still need to register a template for."""
    matches = match_templates_for_surgery(db, surgery)
    matched_procs = {m.matched_procedure for m in matches if not m.is_supplemental}
    return [p for p in _procs_list(surgery) if p not in matched_procs]
