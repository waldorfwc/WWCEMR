"""Regression: a PARTIAL-day blackout must not hide a whole block day.

available_slots_for_surgery computed ONE proposed start (the first gap) and
dropped the entire block day if that start overlapped a blackout — even a
partial-day blackout (e.g. morning PTO). A block day with a morning partial
blackout but a free afternoon was wrongly hidden.

Whole-day blackouts should still remove the day; partial-day blackouts should
only block their own window, and the picker should propose a start AFTER the
blocked window when room remains.
"""
from datetime import date, time, timedelta

from app.models.surgery import BlockDay, Surgery, SurgeryBlackoutDay
from app.services.surgery.date_picker import available_slots_for_surgery

D = date.today() + timedelta(days=21)


def _surgery(db, facility="medstar", kind="robotic_180"):
    s = Surgery(chart_number="1", patient_name="Pat",
                eligible_facilities=[facility], selected_facility=facility,
                status="in_progress", procedure_classification=kind,
                procedures=[{"name": "Hyst", "kind": kind}])
    db.add(s); db.flush()
    return s


def _block(db, d=D, facility="medstar", kind="robotic_180", addon=False):
    bd = BlockDay(facility=facility, block_date=d, block_kind=kind,
                  start_time=time(7, 0), end_time=time(15, 0), is_addon=addon)
    db.add(bd); db.commit(); db.refresh(bd)
    return bd


def _blackout(db, d=D, scope="office", reason="pto",
              start=None, end=None, facility=None, owner_email=None):
    b = SurgeryBlackoutDay(blackout_date=d, scope=scope, reason=reason,
                           start_time=start, end_time=end, facility=facility,
                           owner_email=owner_email)
    db.add(b); db.commit()
    return b


def test_partial_blackout_offers_free_afternoon(db):
    s = _surgery(db); _block(db)            # 07:00–15:00 robotic_180 block
    # Morning PTO 07:00–12:00; 12:00–15:00 (180 min) is still free.
    _blackout(db, scope="office", reason="other",
              start=time(7, 0), end=time(12, 0))
    days = available_slots_for_surgery(db, s)
    match = [a for a in days if a.block_date == D]
    assert match, "block day with a free afternoon was wrongly hidden"
    assert match[0].proposed_start_time >= "12:00", match[0].proposed_start_time


def test_whole_day_blackout_still_excludes(db):
    s = _surgery(db); _block(db)
    _blackout(db, scope="office", reason="holiday")     # whole day
    assert all(a.block_date != D for a in available_slots_for_surgery(db, s))


def test_partial_blackout_no_room_after_window_excludes(db):
    s = _surgery(db); _block(db)
    # 07:00–14:00 blocked on a 07:00–15:00 block → only 1h left < 180min.
    _blackout(db, scope="office", reason="other",
              start=time(7, 0), end=time(14, 0))
    assert all(a.block_date != D for a in available_slots_for_surgery(db, s))
