"""Patient slot-select endpoint coverage (Phase D)."""
from datetime import date, time, timedelta

from app.models.surgery import (
    Surgery, BlockDay, SurgerySlot, SurgeryNote,
)


def _seed(db):
    s = Surgery(
        chart_number="1", patient_name="Pat",
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="in_progress",
        procedures=[{"name": "Hysterectomy", "kind": "robotic_180"}],
    )
    db.add(s); db.flush()
    bd = BlockDay(
        facility="medstar",
        block_date=date.today() + timedelta(days=14),
        block_kind="robotic_180",
        start_time=time(7, 30), end_time=time(15, 0),
    )
    db.add(bd); db.flush()
    return s, bd


def test_select_slot_books_with_template_duration(client, db):
    s, bd = _seed(db)
    # Token-gated endpoint — the test client overrides auth.
    resp = client.post(f"/api/p/surgery/{s.id}/select-slot", json={
        "block_day_id": str(bd.id),
        "start_time": "07:30",
    })
    assert resp.status_code == 200, resp.text
    slot = db.query(SurgerySlot).filter_by(surgery_id=s.id).first()
    assert slot is not None
    assert slot.start_time == time(7, 30)
    assert slot.duration_minutes in (180, 240)  # robotic baseline


def test_select_slot_rejects_busy_time(client, db):
    s, bd = _seed(db)
    # Pre-existing slot at the requested time.
    db.add(SurgerySlot(block_day_id=bd.id, start_time=time(7, 30),
                        duration_minutes=180, procedure_kind="robotic_180"))
    db.commit()

    resp = client.post(f"/api/p/surgery/{s.id}/select-slot", json={
        "block_day_id": str(bd.id), "start_time": "07:30",
    })
    assert resp.status_code == 409


def test_select_slot_writes_audit_note(client, db):
    s, bd = _seed(db)
    client.post(f"/api/p/surgery/{s.id}/select-slot", json={
        "block_day_id": str(bd.id), "start_time": "07:30",
    })
    # SurgeryNote uses `content` (not `body`) — matches existing schema.
    note = db.query(SurgeryNote).filter(SurgeryNote.surgery_id == s.id).first()
    assert note is not None


def test_select_slot_rejects_overlap_not_exact_match(client, db):
    from datetime import date as _d, time as _t, timedelta
    s, bd = _seed(db)
    # Occupy 07:30 for 180 min.
    db.add(SurgerySlot(block_day_id=bd.id, start_time=_t(7, 30),
                        duration_minutes=180, procedure_kind="robotic_180"))
    db.commit()
    # Try to book 08:00 — exact-time match would allow this (BUG), overlap should reject.
    resp = client.post(f"/api/p/surgery/{s.id}/select-slot", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
    })
    assert resp.status_code == 409
