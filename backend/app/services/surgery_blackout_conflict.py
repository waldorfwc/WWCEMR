"""Detect surgeries booked on dates that are now blacked-out.

A conflict is one Surgery whose scheduled_date matches one
SurgeryBlackoutDay row with an applicable scope. Resolved conflicts
(blocked_conflict_notified_at IS NOT NULL) are excluded, as are
cancelled / completed surgeries.

Scope rules:
  office    — applies to any surgery on that date
  facility  — applies to surgeries whose selected_facility == blackout.facility
  provider  — applies to any surgery on that date (single-surgeon practice;
              when we add a second surgeon, swap to email-match)
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.surgery import Surgery, SurgeryBlackoutDay


ACTIVE_STATUSES = ("new", "in_progress", "confirmed", "hold")


def find_blocked_conflicts(db: Session) -> list[dict]:
    """Return one dict per (surgery, blackout) pair."""
    blackouts = db.query(SurgeryBlackoutDay).all()
    if not blackouts:
        return []

    by_date: dict = {}
    for b in blackouts:
        by_date.setdefault(b.blackout_date, []).append(b)

    surgeries = (db.query(Surgery)
                   .filter(Surgery.scheduled_date.in_(list(by_date.keys())))
                   .filter(Surgery.status.in_(ACTIVE_STATUSES))
                   .filter(Surgery.blocked_conflict_notified_at.is_(None))
                   .all())

    out = []
    for s in surgeries:
        for b in by_date[s.scheduled_date]:
            if not _scope_matches(s, b):
                continue
            out.append({
                "surgery_id":       str(s.id),
                "patient_name":     s.patient_name,
                "scheduled_date":   s.scheduled_date.isoformat(),
                "facility":         s.selected_facility,
                "blackout_scope":   b.scope,
                "blackout_reason":  b.reason,
                "blackout_label":   b.label,
            })
            break  # one conflict per surgery is enough
    return out


def _scope_matches(s: Surgery, b: SurgeryBlackoutDay) -> bool:
    if b.scope == "office":
        return True
    if b.scope == "facility":
        return s.selected_facility == b.facility
    if b.scope == "provider":
        # Single-surgeon practice: provider PTO grounds the day for all
        # surgeries. If/when there's >1 operating surgeon, refine this.
        return True
    return False


def is_date_blacked_out(
    db: Session,
    blackout_date,           # date
    facility: str | None,    # surgery's selected facility (used for facility-scope)
) -> "SurgeryBlackoutDay | None":
    """Return the SurgeryBlackoutDay that blocks `blackout_date` for the
    given `facility`, or None if the date is clear.

    Scope rules mirror find_blocked_conflicts:
      office    — applies to any surgery on that date
      facility  — applies only if facility matches blackout.facility
      provider  — applies (single-surgeon practice; same caveat as before)
    """
    rows = (db.query(SurgeryBlackoutDay)
              .filter(SurgeryBlackoutDay.blackout_date == blackout_date).all())
    for b in rows:
        if b.scope == "office":
            return b
        if b.scope == "facility" and facility == b.facility:
            return b
        if b.scope == "provider":
            return b
    return None
