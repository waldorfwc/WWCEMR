"""can_fit parity with legacy hardcoded rules + config override."""
from datetime import time
from types import SimpleNamespace
from app.services.surgery.block_schedule import can_fit, office_slot_times_min
from app.models.surgery_config import SurgeryConfig


def _day(facility, slots=(), start=time(7, 30), end=time(16, 30)):
    mk = lambda kind, mins: SimpleNamespace(procedure_kind=kind, duration_minutes=mins)
    return SimpleNamespace(facility=facility, start_time=start, end_time=end,
                            slots=[mk(k, m) for k, m in slots])


def test_medstar_three_180s_max(db):
    # wide window so the capacity rule (not the time wall) is the gate
    day = _day("medstar", [("robotic_180", 180)] * 3, start=time(7, 0), end=time(20, 0))
    ok, reason = can_fit(db, day, "robotic_180")
    assert not ok and "3" in reason


def test_medstar_no_mixing_180_240(db):
    day = _day("medstar", [("robotic_240", 240)])
    ok, _ = can_fit(db, day, "robotic_180")
    assert not ok


def test_medstar_minor_addon_after_two_robotics(db):
    day = _day("medstar", [("robotic_180", 180)] * 2)
    ok, _ = can_fit(db, day, "minor")
    assert ok


def test_medstar_minor_blocked_at_three(db):
    day = _day("medstar", [("robotic_180", 180)] * 3)
    ok, _ = can_fit(db, day, "minor")
    assert not ok


def test_crmc_six_minors_max(db):
    day = _day("crmc", [("minor", 90)] * 6, start=time(8, 0), end=time(18, 0))
    ok, _ = can_fit(db, day, "minor")
    assert not ok


def test_crmc_no_mix(db):
    day = _day("crmc", [("major", 180)], start=time(8, 0), end=time(16, 0))
    ok, _ = can_fit(db, day, "minor")
    assert not ok


def test_office_seven_slots_default(db):
    assert len(office_slot_times_min(db)) == 7


def test_office_slots_config_override(db):
    db.add(SurgeryConfig(key="capacity_rules", value={
        "office": {"kind": "fixed_slots",
                    "slot_times": ["08:00", "09:00", "10:00"],
                    "case_minutes": 60}}))
    db.commit()
    assert office_slot_times_min(db) == [480, 540, 600]
    day = _day("office", [("office", 60)] * 3, start=time(7, 0), end=time(17, 0))
    ok, reason = can_fit(db, day, "office")
    assert not ok and "3" in reason


def test_time_window_hard_wall(db):
    # 300-minute day already has one 180; a second 180 won't fit
    day = _day("crmc", [("major", 180)], start=time(9, 0), end=time(14, 0))
    ok, reason = can_fit(db, day, "major")
    assert not ok and "minutes" in reason


def test_medstar_capacity_override(db):
    # raise the 180 cap to 4 via config
    db.add(SurgeryConfig(key="capacity_rules", value={
        "medstar": {"kind": "robotic",
                     "options": [{"case_kind": "robotic_180", "max": 4},
                                  {"case_kind": "robotic_240", "max": 2}],
                     "exclusive": True,
                     "minor_addon": {"after_count": 2, "blocked_at": 3}}}))
    db.commit()
    day = _day("medstar", [("robotic_180", 180)] * 3, start=time(7, 0), end=time(20, 0))
    ok, _ = can_fit(db, day, "robotic_180")
    assert ok   # 4th now allowed
