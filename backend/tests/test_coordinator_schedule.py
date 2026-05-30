"""Coordinator schedule-for-patient endpoint (Phase D)."""
from datetime import date, time, timedelta

from app.models.surgery import Surgery, BlockDay, SurgerySlot, SurgeryNote


def _seed(db):
    s = Surgery(chart_number="1", patient_name="Pat",
                 eligible_facilities=["medstar"], selected_facility="medstar",
                 status="in_progress",
                 procedures=[{"name": "Hyst", "kind": "robotic_180"}])
    db.add(s); db.flush()
    bd = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=14),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(17, 0))
    db.add(bd); db.flush()
    return s, bd


def test_coordinator_schedule_default_duration(client, db):
    s, bd = _seed(db)
    resp = client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
    })
    assert resp.status_code == 200, resp.text
    slot = db.query(SurgerySlot).filter_by(surgery_id=s.id).first()
    assert slot.duration_minutes in (180, 240)


def test_coordinator_override_requires_reason_above_10pct(client, db):
    s, bd = _seed(db)
    # 180 min default; 220 is >10% above => reason required.
    resp = client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
        "duration_minutes": 220,
    })
    assert resp.status_code == 422

    resp = client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
        "duration_minutes": 220,
        "override_reason": "Extra complexity",
    })
    assert resp.status_code == 200


def test_coordinator_schedule_writes_note(client, db):
    s, bd = _seed(db)
    client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
    })
    # SurgeryNote uses `content` (no `kind` column). Filter by any
    # mention of coordinator-scheduling. Adapt to your audit pattern.
    notes = db.query(SurgeryNote).filter(SurgeryNote.surgery_id == s.id).all()
    assert len(notes) >= 1
    assert any("coordinator" in (n.content or "").lower()
                or "scheduled" in (n.content or "").lower() for n in notes)


def test_coordinator_schedule_rejects_overlap_not_exact_match(client, db):
    from datetime import time as _t
    s, bd = _seed(db)
    db.add(SurgerySlot(block_day_id=bd.id, start_time=_t(7, 30),
                        duration_minutes=180, procedure_kind="robotic_180"))
    db.commit()
    resp = client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
    })
    assert resp.status_code == 409


def test_coordinator_schedule_rejects_blacked_out_date(client, db):
    from app.models.surgery import SurgeryBlackoutDay
    s, bd = _seed(db)
    db.add(SurgeryBlackoutDay(blackout_date=bd.block_date, scope="office",
                                reason="holiday", label="Memorial Day"))
    db.commit()
    resp = client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
    })
    assert resp.status_code == 409
    assert "blocked" in resp.json()["detail"].lower()
