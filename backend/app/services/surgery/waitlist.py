"""Waitlist matching for surgery slots.

When a surgery gets cancelled (freeing a block day) or a block day
otherwise opens up, this service finds the patients on the waitlist
who could realistically take the slot:

  - Same procedure classification (a robotic_180 patient can't fill an office slot)
  - Eligible facility set includes the freed slot's facility
  - Their `advance_notice_days` ≤ (slot_date - today)
  - Surgery isn't already scheduled, cancelled, or completed
  - Waitlist row hasn't been removed

Matches are ranked by signed_up_at (longest waiting first).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models.surgery import (
    BlockDay, Surgery, SurgerySlot, SurgeryWaitlist,
)


def find_matches(db: Session, *,
                  block_day_id: Optional[str] = None,
                  facility: Optional[str] = None,
                  block_date: Optional[date] = None,
                  procedure_kind: Optional[str] = None) -> list[dict]:
    """Returns a ranked list of waitlisters who could fill the slot.

    Pass either a block_day_id (preferred — auto-derives facility/date/kind)
    or the explicit facility + block_date + procedure_kind triple.
    """
    if block_day_id:
        bd = db.query(BlockDay).filter(BlockDay.id == block_day_id).first()
        if not bd:
            return []
        facility = bd.facility
        block_date = bd.block_date
        # Pick a procedure_kind that fits the block (or pass through if explicit)
        if not procedure_kind:
            procedure_kind = _block_kind_to_proc_kind(bd.block_kind)

    if not (facility and block_date):
        return []

    today = date.today()
    days_until = (block_date - today).days
    if days_until < 0:
        return []

    # Pull active waitlist rows joined with their surgery
    rows = (db.query(SurgeryWaitlist, Surgery)
              .join(Surgery, SurgeryWaitlist.surgery_id == Surgery.id)
              .filter(SurgeryWaitlist.removed_at.is_(None),
                      Surgery.scheduled_date.is_(None),
                      Surgery.status.in_(["new", "in_progress", "hold"]))
              .order_by(SurgeryWaitlist.signed_up_at.asc())
              .all())

    matches = []
    for w, s in rows:
        # Facility eligibility
        if facility not in (s.eligible_facilities or []):
            continue
        # Procedure compatibility
        if procedure_kind and not _proc_kinds_compatible(s.procedure_classification,
                                                          procedure_kind):
            continue
        # Advance notice satisfied?
        if w.advance_notice_days and days_until < w.advance_notice_days:
            continue
        matches.append({
            "waitlist_id": str(w.id),
            "surgery_id": str(s.id),
            "patient_name": s.patient_name,
            "chart_number": s.chart_number,
            "phone": s.cell_phone or s.phone,
            "advance_notice_days": w.advance_notice_days,
            "signed_up_at": w.signed_up_at.isoformat() if w.signed_up_at else None,
            "procedure_classification": s.procedure_classification,
            "procedure_descriptions": [
                p.get("description") for p in (s.procedures or []) if p.get("description")
            ],
            "patient_responsibility": (str(s.patient_responsibility)
                                        if s.patient_responsibility is not None else None),
            "balance_clear": _balance_clear(s),
        })
    return matches


def _balance_clear(s: Surgery) -> bool:
    pr = float(s.patient_responsibility or 0)
    pd = float(s.amount_paid or 0)
    return (pr - pd) <= 0 or s.balance_override


def _proc_kinds_compatible(waitlist_kind: Optional[str],
                            slot_kind: Optional[str]) -> bool:
    """Whether a waitlisted patient's procedure can fill a slot of the
    given kind. Default: exact match. Allow robotic_180 ↔ robotic_240
    flex since those are both robotic blocks."""
    if not waitlist_kind or not slot_kind:
        return True
    if waitlist_kind == slot_kind:
        return True
    robotic = {"robotic_180", "robotic_240"}
    if waitlist_kind in robotic and slot_kind in robotic:
        return True
    return False


def _block_kind_to_proc_kind(block_kind: str) -> Optional[str]:
    """When a generic 'mixed' block opens, we don't restrict by procedure
    kind. For robotic_only / minor_only / major_only blocks, restrict
    matches to that kind."""
    return {
        "robotic_only": "robotic_180",   # also matches robotic_240 via _proc_kinds_compatible
        "minor_only":   "minor",
        "major_only":   "major",
        "office":       "office",
    }.get(block_kind)


def klara_blast_text(facility: str, block_date: date,
                       procedure_kind: Optional[str] = None) -> str:
    facility_label = {
        "medstar": "MedStar Southern Maryland Hospital Center",
        "crmc":    "University of Maryland Charles Regional Medical Center",
        "office":  "our White Plains office",
    }.get(facility, facility)
    proc_label = (procedure_kind or "").replace("_", " ") if procedure_kind else "surgery"
    return (
        f"Hi — this is WWC Surgery Scheduling.\n\n"
        f"We have an open {proc_label} slot at {facility_label} on "
        f"**{block_date.strftime('%A, %B %d, %Y')}**. We're reaching out to "
        f"everyone on the waitlist who indicated they could be ready in time.\n\n"
        f"If you'd like this date, reply **YES — {block_date}** as soon as you can. "
        f"The first patient to confirm gets the slot — others will go back on the list.\n\n"
        f"Reply **NO** if this date doesn't work; you'll stay on the waitlist for "
        f"future openings.\n\n"
        f"— WWC Surgery Scheduling"
    )
