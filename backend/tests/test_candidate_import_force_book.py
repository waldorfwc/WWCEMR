"""Surgery candidate bulk-import — force-book guards (audit #10/#18/#19/#20).

Covers:
  #18 — APPT_TYPE_MAP per-type durations equal block_schedule.DURATIONS
        for the mapped procedure_kind.
  #10 — backfill force-book refuses an OVERLAPPING slot (not just an
        exact same-start collision): 07:30+180 occupies the window, an
        08:00 import must be rejected.
  #20 — the backfill conflict path records a clean message in
        schedule_error_rows (no NameError on CapacityViolation), and
        nothing is double-booked.
"""
from datetime import date, time, timedelta

from app.models.surgery import Surgery, BlockDay, SurgerySlot
from app.services.surgery.candidate_import import import_rows, APPT_TYPE_MAP
from app.services.surgery.block_schedule import DURATIONS


# ─── #18 — duration parity ───────────────────────────────────────────

def test_appt_type_durations_match_durations_table():
    for label, (facility, kind, duration) in APPT_TYPE_MAP.items():
        assert kind in DURATIONS, f"{label} maps to unknown kind {kind}"
        assert duration == DURATIONS[kind], (
            f"{label}: stored duration {duration} != DURATIONS[{kind}]="
            f"{DURATIONS[kind]}")


# ─── shared seeding ──────────────────────────────────────────────────

_DAY = date.today() + timedelta(days=21)


def _seed_medstar_day_with_existing_robotic(db):
    """A MedStar block day 07:30–16:30 with an existing patient booked
    07:30 + 180 min (→ ends 10:30)."""
    bd = BlockDay(facility="medstar", block_date=_DAY,
                   block_kind="robotic_180",
                   start_time=time(7, 30), end_time=time(16, 30))
    db.add(bd); db.flush()
    occupant = Surgery(chart_number="EXIST001", patient_name="Held, Slot",
                        status="confirmed", procedure_classification="robotic_180")
    db.add(occupant); db.flush()
    db.add(SurgerySlot(block_day_id=bd.id, surgery_id=occupant.id,
                        start_time=time(7, 30), duration_minutes=180,
                        procedure_kind="robotic_180"))
    db.commit()
    return bd, occupant


def _seed_pending_candidate(db, chart="NEW0001"):
    """An incomplete + candidate_imported surgery with no scheduled date,
    eligible for backfill re-attempt."""
    s = Surgery(chart_number=chart, patient_name="Pending, Pat",
                 status="incomplete", sub_flag="candidate_imported",
                 scheduled_date=None, procedure_classification="robotic_180")
    db.add(s); db.commit()
    return s


def _row(chart, t):
    return {
        "mrn": chart, "first": "Pat", "last": "Pending",
        "appt_type": "medstar-robot-short",  # robotic_180, 180 min
        "appt_date": _DAY.strftime("%Y-%m-%d"),
        "appt_time": t,
    }


# ─── #10 / #20 — overlap refused on backfill force-book ──────────────

def test_backfill_refuses_overlapping_slot(db):
    bd, occupant = _seed_medstar_day_with_existing_robotic(db)
    _seed_pending_candidate(db, "NEW0001")

    # 08:00 sits inside the existing 07:30–10:30 window → overlap.
    result = import_rows(db, [_row("NEW0001", "8:00 AM")],
                          dry_run=False, by_email="t@x.com",
                          backfill_mode=True)

    # The row must NOT be scheduled; it must land in schedule_error_rows
    # with a clean overlap message (no NameError leaking through errors).
    assert result["scheduled"] == 0
    assert result["schedule_errors"] == 1
    msg = result["schedule_error_rows"][0]["reason"].lower()
    assert "overlap" in msg or "conflict" in msg
    assert "nameerror" not in msg
    # No new slot was created; the occupant's slot is the only one.
    assert db.query(SurgerySlot).filter(
        SurgerySlot.block_day_id == bd.id).count() == 1
    # generic 'errors' bucket (outer except Exception) stayed empty.
    assert result["errors"] == 0


def test_backfill_books_non_overlapping_slot(db):
    """Control: a non-overlapping window (10:30, back-to-back) succeeds,
    proving the refusal above is overlap-specific, not a blanket block."""
    bd, occupant = _seed_medstar_day_with_existing_robotic(db)
    _seed_pending_candidate(db, "NEW0002")

    result = import_rows(db, [_row("NEW0002", "10:30 AM")],
                          dry_run=False, by_email="t@x.com",
                          backfill_mode=True)

    assert result["scheduled"] == 1, result["schedule_error_rows"]
    assert result["schedule_errors"] == 0
    assert db.query(SurgerySlot).filter(
        SurgerySlot.block_day_id == bd.id).count() == 2


def test_backfill_matches_correct_window_on_multiwindow_day(db):
    """#19 — two CRMC windows on the same day (AM 08:00–12:00, PM
    12:30–16:00). An import at 13:00 must book onto the PM window, not an
    arbitrary .first()."""
    am = BlockDay(facility="crmc", block_date=_DAY, block_kind="minor",
                   start_time=time(8, 0), end_time=time(12, 0))
    pm = BlockDay(facility="crmc", block_date=_DAY, block_kind="minor",
                   start_time=time(12, 30), end_time=time(16, 0))
    db.add_all([am, pm]); db.flush()
    s = Surgery(chart_number="CRMC001", patient_name="Pm, Patient",
                 status="incomplete", sub_flag="candidate_imported",
                 scheduled_date=None, procedure_classification="minor")
    db.add(s); db.commit()

    row = {"mrn": "CRMC001", "first": "P", "last": "Pm",
           "appt_type": "crmc-minor", "appt_date": _DAY.strftime("%Y-%m-%d"),
           "appt_time": "1:00 PM"}
    result = import_rows(db, [row], dry_run=False, by_email="t@x.com",
                          backfill_mode=True)

    assert result["scheduled"] == 1, result["schedule_error_rows"]
    slot = db.query(SurgerySlot).filter(SurgerySlot.surgery_id == s.id).one()
    assert slot.block_day_id == pm.id  # PM window, not AM


def test_backfill_refuses_out_of_window_slot(db):
    """Even in backfill_mode a slot outside the block window is refused."""
    bd, occupant = _seed_medstar_day_with_existing_robotic(db)
    _seed_pending_candidate(db, "NEW0003")

    # 15:30 + 180 = 18:30, past the 16:30 block end.
    result = import_rows(db, [_row("NEW0003", "3:30 PM")],
                          dry_run=False, by_email="t@x.com",
                          backfill_mode=True)

    assert result["scheduled"] == 0
    assert result["schedule_errors"] == 1
    assert "window" in result["schedule_error_rows"][0]["reason"].lower()
    assert result["errors"] == 0
