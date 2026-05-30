"""Slot overlap detection — Phase D hotfix."""
from datetime import date, time, timedelta

from app.models.surgery import Surgery, BlockDay, SurgerySlot
from app.services.surgery_slot_conflict import overlapping_slot


def _seed(db, occupy=None):
    """Create a BlockDay; optionally pre-fill one occupied (start, duration)."""
    bd = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=14),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(17, 0))
    db.add(bd); db.flush()
    if occupy:
        st, dur = occupy
        db.add(SurgerySlot(block_day_id=bd.id,
                            start_time=st, duration_minutes=dur,
                            procedure_kind="robotic_180"))
    db.commit()
    return bd


def test_no_existing_slot_no_overlap(db):
    bd = _seed(db)
    assert overlapping_slot(db, bd.id, time(8, 0), 60) is None


def test_exact_match_overlaps(db):
    bd = _seed(db, occupy=(time(7, 30), 180))
    assert overlapping_slot(db, bd.id, time(7, 30), 180) is not None


def test_new_starts_during_existing_overlaps(db):
    # Existing 07:30 + 180 min → ends 10:30. New 08:00 sits inside it.
    bd = _seed(db, occupy=(time(7, 30), 180))
    assert overlapping_slot(db, bd.id, time(8, 0), 60) is not None


def test_new_ends_during_existing_overlaps(db):
    # Existing 09:00 + 60 → 09:00-10:00. New 08:30 + 60 → 08:30-09:30 overlaps.
    bd = _seed(db, occupy=(time(9, 0), 60))
    assert overlapping_slot(db, bd.id, time(8, 30), 60) is not None


def test_new_wraps_existing_overlaps(db):
    # Existing 09:00-10:00. New 08:30 + 120 = 10:30 wraps it.
    bd = _seed(db, occupy=(time(9, 0), 60))
    assert overlapping_slot(db, bd.id, time(8, 30), 120) is not None


def test_back_to_back_does_not_overlap(db):
    # Existing 07:30-10:30. New starts exactly at 10:30.
    bd = _seed(db, occupy=(time(7, 30), 180))
    assert overlapping_slot(db, bd.id, time(10, 30), 60) is None


def test_exclude_slot_id_lets_a_slot_skip_itself(db):
    bd = _seed(db, occupy=(time(7, 30), 180))
    self_slot = db.query(SurgerySlot).first()
    # Querying for the same window with exclude_slot_id should return None.
    assert overlapping_slot(db, bd.id, time(7, 30), 180,
                              exclude_slot_id=self_slot.id) is None
