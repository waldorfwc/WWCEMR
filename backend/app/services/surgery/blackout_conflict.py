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
            # Partial-day blackouts only conflict when the surgery's time
            # window actually overlaps the blackout window.
            if not _time_overlaps_blackout(s, b):
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


def _time_overlaps_blackout(surgery, blackout) -> bool:
    """For partial-day blackouts (start_time/end_time set), only count
    a conflict if the surgery's scheduled window crosses the blackout
    window. Whole-day blackouts (both times None) always conflict.
    """
    if blackout.start_time is None and blackout.end_time is None:
        return True  # whole-day
    if not surgery.start_time:
        return True  # unknown surgery time → conservative: assume conflict
    from datetime import datetime, timedelta
    surg_start = datetime.combine(surgery.scheduled_date, surgery.start_time)
    duration = surgery.duration_minutes or 60
    surg_end = surg_start + timedelta(minutes=duration)
    bk_start = datetime.combine(surgery.scheduled_date,
                                 blackout.start_time)
    bk_end = datetime.combine(surgery.scheduled_date,
                               blackout.end_time)
    # Half-open overlap: starts before the other ends AND ends after the
    # other starts.
    return surg_start < bk_end and surg_end > bk_start


def _scope_matches(s, b):
    if b.scope == "office":
        return True
    if b.scope == "facility":
        return s.selected_facility == b.facility
    if b.scope == "provider":
        # If the blackout names a specific surgeon (owner_email), only match
        # surgeries assigned to that surgeon. If no surgeon is named OR the
        # surgery has no surgeon_email yet, fall back to today's behavior
        # (single-surgeon practice — all surgeries on the date apply).
        if b.owner_email and s.surgeon_email:
            return s.surgeon_email.lower() == b.owner_email.lower()
        return True
    return False


def is_date_blacked_out(
    db,
    blackout_date,
    facility,
    surgeon_email=None,
    start_time=None,
    end_time=None,
):
    """Return the SurgeryBlackoutDay that blocks `blackout_date` for the
    given `facility`, or None if the date is clear.

    Scope rules mirror find_blocked_conflicts:
      office    — applies to any surgery on that date
      facility  — applies only if facility matches blackout.facility
      provider  — applies; if the blackout names a surgeon and surgeon_email
                  is provided, only blocks that surgeon's surgeries

    If `start_time` + `end_time` are passed, a partial-day blackout only
    blocks when its window overlaps the supplied window. Whole-day
    blackouts (start_time/end_time both NULL) always block regardless of
    the input window. With no input window, *any* blackout (whole-day or
    partial) blocks the date — that's the legacy semantics for callers
    that don't know the time yet.
    """
    rows = (db.query(SurgeryBlackoutDay)
              .filter(SurgeryBlackoutDay.blackout_date == blackout_date).all())
    for b in rows:
        scope_match = (
            b.scope == "office"
            or (b.scope == "facility" and facility == b.facility)
            or (b.scope == "provider" and (
                not (b.owner_email and surgeon_email)
                or surgeon_email.lower() == b.owner_email.lower()))
        )
        if not scope_match:
            continue
        if not _windows_overlap(b, start_time, end_time):
            continue
        return b
    return None


def _windows_overlap(blackout, start_time, end_time) -> bool:
    """True if the blackout's window overlaps the supplied window.
    Whole-day blackouts always overlap. If no input window is given,
    any blackout matches (legacy any-time check)."""
    if blackout.start_time is None and blackout.end_time is None:
        return True
    if start_time is None or end_time is None:
        return True
    return start_time < blackout.end_time and end_time > blackout.start_time
