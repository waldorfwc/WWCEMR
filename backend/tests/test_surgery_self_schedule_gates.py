"""Audit #1/#2 regression tests for patient self-schedule slot claim.

Covers:
  - eligible-facility gate (can't book a facility not in eligible_facilities)
  - capacity gate (can_fit must pass)
  - a valid claim still succeeds and creates the slot
  - duration is keyed off the surgery's procedure_classification, not
    block_day.block_kind (robotic_240 -> 240 min, not the generic 60)

Uses the real shared service at app.services.surgery.self_schedule.
"""
from contextlib import ExitStack
from datetime import date, time, timedelta
from unittest.mock import patch

import pytest

from app.models.surgery import Surgery, BlockDay, SurgerySlot
from app.services.surgery.self_schedule import (
    claim_slot_for_patient, SelfScheduleError,
)

MOD = "app.services.surgery.self_schedule"


def _mute_side_effects():
    """Silence the post-commit side effects (calendar, email, boldsign)
    so the tests exercise only the booking gates."""
    stack = ExitStack()
    stack.enter_context(patch(f"{MOD}.upsert_event_for_surgery"))
    stack.enter_context(patch(f"{MOD}._send_surgery_confirmation_email"))
    stack.enter_context(
        patch("app.services.boldsign_envelopes.send_consent_envelopes")
    )
    return stack


def _seed_surgery(db, *, facility="medstar", proc="robotic_240",
                  eligible=None, duration=None):
    s = Surgery(
        chart_number="1", patient_name="Pat",
        eligible_facilities=eligible if eligible is not None else [facility],
        status="new",
        procedure_classification=proc,
        duration_minutes=duration,
    )
    db.add(s); db.commit(); db.refresh(s)
    return s


def _seed_block(db, *, facility="medstar", block_kind="robotic_only",
                days_out=14, start=time(7, 0), end=time(19, 0)):
    bd = BlockDay(
        block_date=date.today() + timedelta(days=days_out),
        facility=facility,
        start_time=start, end_time=end,
        block_kind=block_kind,
    )
    db.add(bd); db.commit(); db.refresh(bd)
    return bd


def _add_slot(db, bd, *, proc, start, duration):
    sl = SurgerySlot(
        block_day_id=bd.id, surgery_id=None,
        start_time=start, duration_minutes=duration,
        procedure_kind=proc,
    )
    db.add(sl); db.commit()
    return sl


def test_claim_rejects_ineligible_facility(db):
    # Surgery only eligible for office, claiming a medstar block.
    s = _seed_surgery(db, facility="medstar", proc="robotic_240",
                      eligible=["office"])
    bd = _seed_block(db, facility="medstar")
    with _mute_side_effects():
        with pytest.raises(SelfScheduleError) as ei:
            claim_slot_for_patient(
                db, s, block_day_id=str(bd.id),
                start_time_str="07:00", sent_by="patient:portal",
            )
    assert ei.value.status_code == 409
    assert "facility" in str(ei.value).lower()
    # No slot created.
    assert db.query(SurgerySlot).filter(
        SurgerySlot.surgery_id == s.id).count() == 0


def test_claim_rejects_when_over_capacity(db):
    # medstar robotic_240 max is 2; pre-fill 2 so a 3rd can't fit.
    s = _seed_surgery(db, facility="medstar", proc="robotic_240",
                      eligible=["medstar"])
    bd = _seed_block(db, facility="medstar")
    _add_slot(db, bd, proc="robotic_240", start=time(7, 0), duration=240)
    _add_slot(db, bd, proc="robotic_240", start=time(11, 0), duration=240)
    db.refresh(bd)
    with _mute_side_effects():
        with pytest.raises(SelfScheduleError) as ei:
            claim_slot_for_patient(
                db, s, block_day_id=str(bd.id),
                start_time_str="15:00", sent_by="patient:portal",
            )
    assert ei.value.status_code == 409
    # The surgery did not get a slot.
    assert db.query(SurgerySlot).filter(
        SurgerySlot.surgery_id == s.id).count() == 0


def test_valid_claim_succeeds_and_creates_slot(db):
    s = _seed_surgery(db, facility="medstar", proc="robotic_240",
                      eligible=["medstar"])
    bd = _seed_block(db, facility="medstar")
    with _mute_side_effects():
        result = claim_slot_for_patient(
            db, s, block_day_id=str(bd.id),
            start_time_str="07:00", sent_by="patient:portal",
        )
    db.refresh(s)
    assert result["block_day_id"] == str(bd.id)
    assert s.scheduled_date == bd.block_date
    assert s.selected_facility == "medstar"
    slot = db.query(SurgerySlot).filter(
        SurgerySlot.surgery_id == s.id).one()
    assert slot.procedure_kind == "robotic_240"


def test_duration_keys_off_procedure_classification_not_block_kind(db):
    # robotic_240 with no explicit duration must store 240, not 60.
    s = _seed_surgery(db, facility="medstar", proc="robotic_240",
                      eligible=["medstar"], duration=None)
    bd = _seed_block(db, facility="medstar")
    with _mute_side_effects():
        result = claim_slot_for_patient(
            db, s, block_day_id=str(bd.id),
            start_time_str="07:00", sent_by="patient:portal",
        )
    assert result["duration_minutes"] == 240
    slot = db.query(SurgerySlot).filter(
        SurgerySlot.surgery_id == s.id).one()
    assert slot.duration_minutes == 240
