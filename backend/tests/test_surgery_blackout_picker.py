"""Surgery date picker + materializer must honor blackouts.

Regression: a block day on a date that gets blacked out AFTER the block was
materialized (the typical ad-hoc PTO case) used to stay selectable in the
date picker, because available_slots_for_surgery never filtered blackouts and
the materializer only skipped *creating* new block days (never deleted stale
ones).
"""
from datetime import date, time, timedelta

import pytest

from app.models.surgery import (BlockDay, BlockSchedule, Surgery,
                                SurgeryBlackoutDay, SurgerySlot)
from app.services.surgery.date_picker import (available_slots_for_surgery,
                                             pick_or_reschedule, DatePickerError)
from app.services.surgery.block_schedule import materialize_block_days

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


def _blackout(db, d=D, scope="provider", reason="pto",
              start=None, end=None, facility=None, owner_email=None):
    b = SurgeryBlackoutDay(blackout_date=d, scope=scope, reason=reason,
                           start_time=start, end_time=end, facility=facility,
                           owner_email=owner_email)
    db.add(b); db.commit()
    return b


# ── Fix #1: offer list filters blackouts ────────────────────────────

def test_offer_includes_day_without_blackout(db):
    s = _surgery(db); _block(db)
    days = available_slots_for_surgery(db, s)
    assert any(a.block_date == D for a in days)


def test_offer_excludes_whole_day_provider_pto(db):
    s = _surgery(db); _block(db)
    _blackout(db, scope="provider", reason="pto")        # whole-day PTO
    days = available_slots_for_surgery(db, s)
    assert all(a.block_date != D for a in days)


def test_offer_excludes_office_holiday(db):
    s = _surgery(db); _block(db)
    _blackout(db, scope="office", reason="holiday")
    assert all(a.block_date != D for a in available_slots_for_surgery(db, s))


def test_offer_facility_blackout_scoped_to_that_facility(db):
    s = _surgery(db, facility="medstar")
    _block(db, facility="medstar")
    _blackout(db, scope="facility", reason="facility_closed", facility="crmc")
    # A CRMC closure must NOT hide a MedStar block.
    assert any(a.block_date == D for a in available_slots_for_surgery(db, s))
    _blackout(db, scope="facility", reason="facility_closed", facility="medstar")
    assert all(a.block_date != D for a in available_slots_for_surgery(db, s))


def test_offer_partial_blackout_overlapping_proposed_pushes_start(db):
    s = _surgery(db); _block(db)
    # A 07:00–12:00 partial blackout overlaps the front of the day, but the
    # 12:00–15:00 afternoon still fits a 180-min case → the day stays offered
    # with the proposed start pushed past the blocked window (regression: it
    # used to compute one 07:00 start and drop the whole day).
    _blackout(db, scope="office", reason="other",
              start=time(7, 0), end=time(12, 0))
    days = available_slots_for_surgery(db, s)
    match = [a for a in days if a.block_date == D]
    assert match
    assert match[0].proposed_start_time >= "12:00"


def test_offer_partial_blackout_not_overlapping_kept(db):
    s = _surgery(db); _block(db)
    # 13:00–15:00 blackout doesn't touch the 07:00–10:00 proposed slot.
    _blackout(db, scope="office", reason="other",
              start=time(13, 0), end=time(15, 0))
    assert any(a.block_date == D for a in available_slots_for_surgery(db, s))


def test_booking_rejects_blacked_out_day(db):
    s = _surgery(db); bd = _block(db)
    _blackout(db, scope="provider", reason="pto")
    with pytest.raises(DatePickerError):
        pick_or_reschedule(db, s, block_day_id=str(bd.id), picked_by="t@x")


# ── Fix #2a: materializer removes stale empty block days ────────────

def test_materializer_removes_empty_blockday_on_blackout(db):
    _block(db)                                   # schedule-derived, no slots
    _blackout(db, scope="office", reason="holiday")
    out = materialize_block_days(db, days_ahead=60)
    assert out["blockdays_removed"] >= 1
    assert db.query(BlockDay).filter(BlockDay.block_date == D).count() == 0


def test_create_blackout_endpoint_clears_stale_block_day(client, db):
    _block(db)
    assert db.query(BlockDay).filter(BlockDay.block_date == D).count() == 1
    resp = client.post("/api/surgery/admin/blackouts", json={
        "blackout_date": D.isoformat(),
        "scope": "office", "reason": "holiday", "label": "Test Holiday",
    })
    assert resp.status_code == 201, resp.text
    # Whole-day office blackout → the stale empty block day is reconciled away.
    assert db.query(BlockDay).filter(BlockDay.block_date == D).count() == 0


def test_materializer_keeps_blockday_with_booked_slot(db):
    bd = _block(db)
    db.add(SurgerySlot(block_day_id=bd.id, start_time=time(7, 0),
                       duration_minutes=180, procedure_kind="robotic_180"))
    db.commit()
    _blackout(db, scope="office", reason="holiday")
    materialize_block_days(db, days_ahead=60)
    # Has a real booking → must NOT be silently deleted (it's a conflict to
    # resolve, not a stale empty block).
    assert db.query(BlockDay).filter(BlockDay.id == bd.id).count() == 1
