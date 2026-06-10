"""Pellet patient workflow — milestone catalogs + helpers.

Milestone catalogs vary by (visit_kind, patient_type):
  initial + new          — full intake including consultation + Dosagio dose
  initial + established  — skip consultation, dose carried forward from prior visit
  booster | repeat       — light flow: schedule, bag, insert, bill
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.pellet import (
    PelletVisit, PelletVisitMilestone, DEFAULT_PRICE_NEW, DEFAULT_PRICE_ESTABLISHED,
)


# Milestone catalogs — (kind, title, position)
INITIAL_NEW = [
    ("consultation",         "Consultation — patient decides to proceed", 1),
    ("interest_confirmed",   "Interest confirmation call",                2),
    ("mammo_verified",       "Mammogram verified (BI-RADS 1 or 2)",       3),
    ("labs_verified",        "Labs verified (FSH, TSH, Estradiol)",       4),
    ("dosed_in_dosagio",     "Dose calculated in Dosagio + recorded",     5),
    ("payment_collected",    "Payment collected ($500 via Klara → ModMed)", 6),
    ("scheduled",            "Insertion appointment scheduled in ModMed", 7),
    ("bagged",               "Pellets pre-bagged (Tattiana)",             8),
    ("inserted",             "Pellets inserted",                          9),
    ("billed",               "Billed (claim # recorded)",                10),
]

INITIAL_ESTABLISHED = [
    ("interest_confirmed",   "Interest confirmation call",                1),
    ("mammo_verified",       "Mammogram verified (BI-RADS 1 or 2)",       2),
    ("labs_verified",        "Labs verified (FSH, TSH, Estradiol)",       3),
    ("dose_set_from_prior",  "Dose set from prior visit (no Dosagio)",    4),
    ("payment_collected",    "Payment collected ($400 via Klara → ModMed)", 5),
    ("scheduled",            "Insertion appointment scheduled in ModMed", 6),
    ("bagged",               "Pellets pre-bagged (Tattiana)",             7),
    ("inserted",             "Pellets inserted",                          8),
    ("billed",               "Billed (claim # recorded)",                 9),
]

# Booster / repeat — short flow (no consultation, no relabs unless overdue,
# dose carried forward)
SHORT_FLOW = [
    ("dose_set_from_prior",  "Dose carried forward (adjusted if needed)", 1),
    ("payment_collected",    "Payment collected (Klara → ModMed)",        2),
    ("scheduled",            "Insertion appointment scheduled in ModMed", 3),
    ("bagged",               "Pellets pre-bagged",                        4),
    ("inserted",             "Pellets inserted",                          5),
    ("billed",               "Billed (claim # recorded)",                 6),
]


def default_price_for(patient_type: str) -> float:
    return float(DEFAULT_PRICE_NEW if patient_type == "new"
                                    else DEFAULT_PRICE_ESTABLISHED)


def milestone_catalog(visit_kind: str, patient_type: str) -> list[tuple]:
    if visit_kind == "initial":
        return INITIAL_NEW if patient_type == "new" else INITIAL_ESTABLISHED
    return SHORT_FLOW


def spawn_milestones(db: Session, v: PelletVisit, patient_type: str) -> None:
    """Create the milestone catalog for this visit. Idempotent — skips
    if milestones already exist on the visit."""
    if v.milestones:
        return
    catalog = milestone_catalog(v.visit_kind, patient_type)
    for kind, title, pos in catalog:
        db.add(PelletVisitMilestone(
            visit_id=v.id, kind=kind, title=title, position=pos,
            status="pending",
        ))


def patient_buckets(p, today=None) -> set[str]:
    """Return workflow buckets for a patient's current state. For now,
    derive from the latest visit's milestones + status."""
    from datetime import date as _date
    today = today or _date.today()
    out: set[str] = set()
    if not p.visits:
        return {"new_no_visit"}
    v = p.visits[0]   # ordered by created_at desc
    if v.status in ("cancelled",):
        out.add("cancelled")
        return out
    if v.status == "billed":
        out.add("completed")
        return out
    out.add("outstanding")

    # Map milestones into buckets
    by_kind = {m.kind: m for m in (v.milestones or [])}
    def done(kind: str) -> bool:
        m = by_kind.get(kind)
        return m is not None and m.status in ("done", "skipped", "not_applicable")

    if not done("mammo_verified") and "mammo_verified" in by_kind:
        out.add("needs_mammo")
    if not done("labs_verified") and "labs_verified" in by_kind:
        out.add("needs_labs")
    if not done("payment_collected"):
        out.add("needs_payment")
    if done("payment_collected") and not done("scheduled"):
        out.add("needs_schedule")
    if done("scheduled") and not done("bagged"):
        out.add("needs_bagging")
    if done("bagged") and not done("inserted"):
        out.add("appt_today")
    if done("inserted") and not done("billed"):
        out.add("needs_billing")
    return out
