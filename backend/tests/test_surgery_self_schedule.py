"""Shared slot-claim service — used by magic-link + portal flows."""
from datetime import date, time, timedelta
from unittest.mock import patch
from app.models.surgery import Surgery, BlockDay
from app.services.surgery_self_schedule import (
    claim_slot_for_patient, SelfScheduleError,
)


def _seed_bd(db, *, facility="office", days_out=14):
    bd = BlockDay(
        block_date=date.today() + timedelta(days=days_out),
        facility=facility,
        start_time=time(8, 0), end_time=time(15, 0),
        block_kind="office_d_and_c",
    )
    db.add(bd); db.commit(); db.refresh(bd)
    return bd


def _seed_s(db):
    s = Surgery(
        chart_number="1", patient_name="Pat",
        eligible_facilities=["office"], status="new",
        procedure_classification="office_d_and_c",
        estimated_minutes=60,
    )
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_claim_books_the_slot_and_stamps_surgery(db):
    s = _seed_s(db); bd = _seed_bd(db)
    with patch("app.services.surgery_self_schedule.upsert_event_for_surgery"):
        with patch("app.services.surgery_self_schedule._send_surgery_confirmation_email"):
            result = claim_slot_for_patient(
                db, s, block_day_id=str(bd.id),
                start_time_str="08:00",
                sent_by="portal:e2e-test",
            )
    db.refresh(s)
    assert s.scheduled_date == bd.block_date
    assert s.selected_facility == "office"
    assert s.scheduled_start_time == time(8, 0)
    assert result["start_time"] == "08:00"
    assert result["block_day_id"] == str(bd.id)


def test_claim_raises_on_blackout(db, monkeypatch):
    s = _seed_s(db); bd = _seed_bd(db)
    # Force is_date_blacked_out to return a truthy "blackout" object
    from collections import namedtuple
    BO = namedtuple("BO", "label reason scope")
    monkeypatch.setattr(
        "app.services.surgery_self_schedule.is_date_blacked_out",
        lambda db, d, fac, surg_email: BO("Doctor away", None, "surgeon"),
    )
    try:
        claim_slot_for_patient(db, s, block_day_id=str(bd.id),
                                  start_time_str="08:00", sent_by="x")
    except SelfScheduleError as e:
        assert "Doctor away" in str(e) or "blocked" in str(e).lower()
        return
    raise AssertionError("expected SelfScheduleError")


def test_gate_passes_when_pt_resp_is_zero(db):
    from app.services.surgery_self_schedule import schedule_gate_for_surgery
    s = _seed_s(db); s.patient_responsibility = 0; db.commit()
    allowed, reason = schedule_gate_for_surgery(s)
    assert allowed is True and reason is None


def test_gate_blocks_when_unpaid(db):
    from app.services.surgery_self_schedule import schedule_gate_for_surgery
    s = _seed_s(db); s.patient_responsibility = 250; s.amount_paid = 0
    db.commit()
    allowed, reason = schedule_gate_for_surgery(s)
    assert allowed is False
    assert "$250.00" in reason


def test_gate_passes_when_fully_paid(db):
    from app.services.surgery_self_schedule import schedule_gate_for_surgery
    s = _seed_s(db); s.patient_responsibility = 250; s.amount_paid = 250
    db.commit()
    allowed, reason = schedule_gate_for_surgery(s)
    assert allowed is True and reason is None


def test_gate_passes_when_coordinator_overrides(db):
    from app.services.surgery_self_schedule import schedule_gate_for_surgery
    s = _seed_s(db); s.patient_responsibility = 250; s.amount_paid = 0
    s.schedule_gate_override = True; db.commit()
    allowed, reason = schedule_gate_for_surgery(s)
    assert allowed is True


def test_claim_triggers_boldsign_send(db):
    from unittest.mock import patch
    s = _seed_s(db); bd = _seed_bd(db)
    with patch("app.services.surgery_self_schedule.upsert_event_for_surgery"), \
         patch("app.services.surgery_self_schedule._send_surgery_confirmation_email"), \
         patch("app.services.boldsign_envelopes.send_consent_envelopes") as mock_send:
        claim_slot_for_patient(
            db, s, block_day_id=str(bd.id),
            start_time_str="08:00", sent_by="patient:portal",
        )
    mock_send.assert_called_once()
    # Confirm sent_by is propagated so the audit trail captures who scheduled
    _, kwargs = mock_send.call_args
    assert kwargs.get("sent_by") == "patient:portal"


def test_claim_succeeds_when_boldsign_send_fails(db):
    """BoldSign outage must not block the booking."""
    from unittest.mock import patch
    s = _seed_s(db); bd = _seed_bd(db)
    with patch("app.services.surgery_self_schedule.upsert_event_for_surgery"), \
         patch("app.services.surgery_self_schedule._send_surgery_confirmation_email"), \
         patch("app.services.boldsign_envelopes.send_consent_envelopes",
                side_effect=Exception("BoldSign 503")):
        result = claim_slot_for_patient(
            db, s, block_day_id=str(bd.id),
            start_time_str="08:00", sent_by="patient:portal",
        )
    # The slot was still claimed
    assert result["start_time"] == "08:00"
    db.refresh(s)
    assert s.scheduled_date == bd.block_date
