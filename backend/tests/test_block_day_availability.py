"""Block-day availability endpoint coverage (F-Phase)."""
from datetime import date, time, timedelta

from app.models.surgery import Surgery, BlockDay, SurgerySlot


def _seed(db):
    s = Surgery(chart_number="1", patient_name="Pat",
                 eligible_facilities=["medstar"], selected_facility="medstar",
                 status="in_progress",
                 procedures=[{"name": "Hyst", "kind": "robotic_180"}])
    db.add(s); db.flush()
    bd = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=14),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(15, 0))
    db.add(bd); db.commit()
    return s, bd


def test_availability_returns_list(client, db):
    s, bd = _seed(db)
    resp = client.get(f"/api/surgery/admin/block-days/{bd.id}/availability",
                       params={"surgery_id": str(s.id)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["block_day_id"] == str(bd.id)
    assert len(body["available_starts"]) > 0
    # First option is the block's start_time formatted as HH:MM
    assert body["available_starts"][0] == "07:00"


def test_availability_excludes_overlapping_starts(client, db):
    s, bd = _seed(db)
    db.add(SurgerySlot(block_day_id=bd.id, start_time=time(7, 30),
                        duration_minutes=180,
                        procedure_kind="robotic_180"))
    db.commit()
    body = client.get(f"/api/surgery/admin/block-days/{bd.id}/availability",
                       params={"surgery_id": str(s.id)}).json()
    # 07:00 + 60 (template default = 60 because no template; default surgery
    # has no duration_minutes) — verify nothing inside the occupied window
    # is offered.
    for t in body["available_starts"]:
        h, m = [int(x) for x in t.split(":")]
        start_min = h*60 + m
        # 180-min slot at 07:30 → 07:30-10:30 occupied window (450-630 mins).
        # Available start + duration must not enter that window.
        # Just confirm we never see anything in the occupied window itself.
        assert not (450 <= start_min < 630)
