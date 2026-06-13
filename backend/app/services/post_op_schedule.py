"""Post-op appointment schedule determination.

Maps a Surgery's procedures to the list of required follow-up
appointments. Procedure keywords are matched substring-wise,
case-insensitive. When a surgery includes multiple procedures, the
longest / most-demanding schedule wins (e.g. D&C + hysterectomy →
hysterectomy schedule).

Practice-defined rules (Phase 3):
  Hysterectomy        — 1 week + 6 weeks
  Myomectomy          — 1 week + 4 weeks
  Laparoscopy         — 1 week
  LEEP                — 2 weeks
  D&C / Hysteroscopy  — 2 weeks
  Ablation            — 2 months (≈60 days)

Returns a list of (label, days_post_op) pairs ordered by days. The
frontend renders one date picker per entry; the milestone auto-completes
when every entry has a date filled in.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from app.models.surgery import Surgery


@dataclass
class PostOpVisit:
    label: str               # human label, e.g. "1 week post-op"
    days_post_op: int        # expected days after surgery
    suggested_location: str = "office"   # "office" | "telehealth"
    location_locked: bool = False        # True = clinically required in-office


# Ordered most-demanding → least. First match in this list wins for each
# procedure name. A surgery's overall schedule = union of all matches,
# de-duplicated by label.
#
# Default location convention: the first/early visit is in-office
# (wound + suture check, vitals, sometimes labs). Later visits are
# typically a general check-in and run fine over telehealth.
#
# A few visits are CLINICALLY REQUIRED to be in person — flagged with
# location_locked=True so the coordinator can't accidentally book them
# as telehealth.
DEFAULT_PROCEDURE_RULES: list[tuple[list[str], list[PostOpVisit]]] = [
    # Hysterectomy variants (TAH, TLH, LAVH, robotic, supracervical, etc.)
    (["hysterectomy"], [
        PostOpVisit("1 week post-op", 7, "office"),
        # 6-week hysterectomy visit must be in-person (cuff check).
        PostOpVisit("6 weeks post-op", 42, "office", location_locked=True),
    ]),
    # Myomectomy
    (["myomectomy"], [
        PostOpVisit("1 week post-op", 7, "office"),
        PostOpVisit("4 weeks post-op", 28, "telehealth"),
    ]),
    # Endometrial ablation (NovaSure, ThermaChoice, etc.)
    (["ablation"], [
        PostOpVisit("2 months post-op", 60, "telehealth"),
    ]),
    # LEEP — 2-week visit must be in-person (margin/biopsy follow-up).
    (["leep"], [
        PostOpVisit("2 weeks post-op", 14, "office", location_locked=True),
    ]),
    # D&C / Hysteroscopy
    (["d&c", "dilation", "dilatation", "hysteroscopy"], [
        PostOpVisit("2 weeks post-op", 14, "office"),
    ]),
    # Bare laparoscopy (when no more-specific match above caught it)
    (["laparoscopy", "laparoscopic"], [
        PostOpVisit("1 week post-op", 7, "office"),
    ]),
]


def _procs_list(s: Surgery) -> list[str]:
    procs = s.procedures or []
    if isinstance(procs, str):
        try:
            procs = json.loads(procs)
        except Exception:
            procs = [procs]
    out: list[str] = []
    for p in procs:
        if isinstance(p, dict):
            desc = p.get("description") or ""
        else:
            desc = str(p)
        if desc:
            out.append(desc)
    return out


def rules_from_config(db):
    """Config-driven rules; falls back to DEFAULT_PROCEDURE_RULES when the
    key is unset, db is None, or the stored JSON is malformed."""
    if db is None:
        return DEFAULT_PROCEDURE_RULES
    try:
        from app.services.surgery.settings import cfg
        raw = cfg(db, "post_op_schedules")
        if not raw:
            return DEFAULT_PROCEDURE_RULES
        out = []
        for rule in raw:
            visits = [PostOpVisit(
                label=v["label"],
                days_post_op=int(v["offset_days"]),
                suggested_location=v.get("mode", "office"),
                location_locked=bool(v.get("location_locked", False)),
            ) for v in rule["visits"]]
            out.append(([k.lower() for k in rule["match"]], visits))
        return out
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "bad post_op_schedules config; using defaults", exc_info=True)
        return DEFAULT_PROCEDURE_RULES


def determine_post_op_schedule(s: Surgery, db=None) -> list[PostOpVisit]:
    """Return the set of post-op visits needed for this surgery.
    Empty list = no procedure recognized; staff can manually pick a
    schedule by entering a single appt date."""
    visits: dict[str, PostOpVisit] = {}     # keyed by label, dedupes across procedures
    procs = _procs_list(s)
    if not procs:
        return []
    proc_text = " ".join(procs).lower()

    for keywords, vlist in rules_from_config(db):
        # Skip the bare "laparoscopy" rule if a more-specific match already fired.
        # We do this by checking whether 'hysterectomy' or 'myomectomy' is in the
        # same procedure text — those rules already added 1-week + further visits.
        if "laparoscopy" in keywords or "laparoscopic" in keywords:
            if "hysterectomy" in proc_text or "myomectomy" in proc_text:
                continue
        # Hysteroscopy-with-IUD-insertion gets no auto post-op (one-and-done).
        # Only excludes the hysteroscopy-side trigger — D&C with IUD still gets
        # the 2-week visit on the D&C side.
        if "hysteroscopy" in keywords and "iud" in proc_text:
            has_dc = any(k in proc_text for k in ("d&c", "dilation", "dilatation"))
            if not has_dc:
                continue
        if any(kw in proc_text for kw in keywords):
            for v in vlist:
                visits.setdefault(v.label, v)

    return sorted(visits.values(), key=lambda v: v.days_post_op)


def all_required_appts_filled(s: Surgery, db=None) -> bool:
    """True when every required post-op visit date is set on the surgery."""
    visits = determine_post_op_schedule(s, db=db)
    if len(visits) == 0:
        # Fallback: at least the first appt date must be set (matches legacy
        # needs_followup_appt logic).
        return s.post_op_appt_date is not None
    if len(visits) == 1:
        return s.post_op_appt_date is not None
    # 2 visits required
    return s.post_op_appt_date is not None and s.post_op_appt_2nd_date is not None
