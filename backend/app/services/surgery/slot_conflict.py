"""Slot overlap detection for the booking endpoints."""
from __future__ import annotations

from datetime import time

from sqlalchemy.orm import Session

from app.models.surgery import SurgerySlot


def _to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def overlapping_slot(
    db: Session,
    block_day_id,
    start: time,
    duration_minutes: int,
    exclude_slot_id=None,
) -> SurgerySlot | None:
    """Return the existing slot that overlaps `[start, start+duration)` on
    `block_day_id`, or None if no overlap. `exclude_slot_id` lets the caller
    ignore the slot being modified (used by the duration PATCH endpoint)."""
    if duration_minutes <= 0:
        return None
    new_start = _to_minutes(start)
    new_end = new_start + duration_minutes
    rows = (db.query(SurgerySlot)
              .filter(SurgerySlot.block_day_id == block_day_id).all())
    for s in rows:
        if exclude_slot_id is not None and s.id == exclude_slot_id:
            continue
        ex_start = _to_minutes(s.start_time)
        ex_end = ex_start + (s.duration_minutes or 0)
        # Overlap iff intervals are not disjoint.
        if new_start < ex_end and ex_start < new_end:
            return s
    return None
