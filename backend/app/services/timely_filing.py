"""Per-payer timely-filing windows.

WWC-specific defaults from the practice. Pattern-matches the messy insurance
company names from the unpaid-claims export. Defaults to 180 days for any
unknown payer (the most common MCO window).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Tuple


# Order matters — more specific patterns first.
_PAYER_RULES: list[tuple[tuple[str, ...], int]] = [
    # Each entry: (keywords_all_lowercased, days_allowed)
    # UHC Community Plans / Medicaid arms — 180 days (must come before generic UHC)
    (("uhc", "community"),           180),
    (("united health", "community"), 180),
    (("uhc", "medicaid"),            180),
    (("united", "medicaid"),         180),
    # Regular UHC (90 — set lower than published 180 because UHC denies early)
    (("uhc",),                       90),
    (("united health",),             90),
    (("umr",),                       90),
    (("optimum choice",),            90),
    # BCBS family — 365 days
    (("bcbs",),                      365),
    (("carefirst",),                 365),
    (("blue cross",),                365),
    (("blue shield",),               365),
    # Medicare straight (not Advantage) — 365 days
    (("medicare advantage",),        180),  # treat MA like an MCO
    (("medicare",),                  365),
    # Aetna — 90 days
    (("aetna",),                     90),
    # Tricare — 180 days
    (("tricare",),                   180),
    # MCO names common to MD/DC region — 180 days
    (("priority partners",),         180),
    (("wellpoint",),                 180),
    (("amerigroup",),                180),
    (("medstar family choice",),     180),
    (("medstar",),                   180),
    (("kaiser",),                    180),
    (("cigna",),                     90),
    (("humana",),                    180),
    # Generic MCO / Medicaid catch-alls — 180 days
    (("mco",),                       180),
    (("medicaid",),                  180),
]


_DEFAULT_DAYS = 180


def days_allowed_for(insurance_company: Optional[str]) -> int:
    """Return the timely-filing window (days) for the given payer name.
    Defaults to 180 when the payer is unknown/missing."""
    if not insurance_company:
        return _DEFAULT_DAYS
    s = insurance_company.lower()
    for keywords, days in _PAYER_RULES:
        if all(k in s for k in keywords):
            return days
    return _DEFAULT_DAYS


def timely_filing_info(insurance_company: Optional[str], dos: Optional[date]) -> dict:
    """Compute timely-filing summary for a single claim.

    Returns a dict with:
      tf_days_allowed:        int          (e.g. 365)
      tf_deadline_date:       date | None  (DOS + days_allowed)
      days_until_tf_deadline: int | None   (negative if past deadline)
      tf_status:              str          ('past' | 'urgent' | 'soon' | 'safe' | 'unknown')

    Status thresholds:
      past   = deadline already passed (≤ 0 days remaining)
      urgent = ≤ 14 days remaining
      soon   = 15–30 days remaining
      safe   = > 30 days remaining
      unknown = no DOS available
    """
    days_allowed = days_allowed_for(insurance_company)
    if dos is None:
        return {
            "tf_days_allowed": days_allowed,
            "tf_deadline_date": None,
            "days_until_tf_deadline": None,
            "tf_status": "unknown",
        }
    deadline = dos + timedelta(days=days_allowed)
    days_remaining = (deadline - date.today()).days
    if days_remaining <= 0:
        status = "past"
    elif days_remaining <= 14:
        status = "urgent"
    elif days_remaining <= 30:
        status = "soon"
    else:
        status = "safe"
    return {
        "tf_days_allowed": days_allowed,
        "tf_deadline_date": deadline,
        "days_until_tf_deadline": days_remaining,
        "tf_status": status,
    }
