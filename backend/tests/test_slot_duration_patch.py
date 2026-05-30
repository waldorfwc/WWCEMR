"""Slot duration patch endpoint (Phase D)."""
from datetime import date, time, timedelta

from app.models.surgery import Surgery, BlockDay, SurgerySlot, SurgeryNote


def _seed_with_slot(db, dur=180):
    s = Surgery(chart_number="1", patient_name="Pat",
                 eligible_facilities=["medstar"], selected_facility="medstar",
                 status="confirmed",
                 procedures=[{"name": "Hyst", "kind": "robotic_180"}])
    db.add(s); db.flush()
    bd = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=14),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(17, 0))
    db.add(bd); db.flush()
    slot = SurgerySlot(block_day_id=bd.id, surgery_id=s.id,
                        start_time=time(8, 0), duration_minutes=dur,
                        procedure_kind="robotic_180")
    db.add(slot); db.commit()
    return s, slot


def test_patch_slot_duration(client, db):
    s, slot = _seed_with_slot(db)
    resp = client.patch(f"/api/surgery/slots/{slot.id}", json={
        "duration_minutes": 210,
        "override_reason": "Extended OR time approved",
    })
    assert resp.status_code == 200, resp.text
    db.refresh(slot)
    assert slot.duration_minutes == 210


def test_patch_slot_requires_reason(client, db):
    s, slot = _seed_with_slot(db)
    resp = client.patch(f"/api/surgery/slots/{slot.id}", json={
        "duration_minutes": 210,
    })
    assert resp.status_code == 422


def test_patch_slot_writes_note(client, db):
    s, slot = _seed_with_slot(db)
    client.patch(f"/api/surgery/slots/{slot.id}", json={
        "duration_minutes": 210, "override_reason": "Extra time"})
    notes = db.query(SurgeryNote).filter(SurgeryNote.surgery_id == s.id).all()
    assert len(notes) >= 1
    # Note content should reference both old and new duration
    n = notes[-1]
    assert "180" in (n.content or "")
    assert "210" in (n.content or "")
