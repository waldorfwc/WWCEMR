from datetime import date, time
from app.models.pellet_schedule import PelletAvailabilityTemplate, PelletSlot


def test_create_template_materializes(client, db):
    r = client.post("/api/pellets/availability/templates", json={
        "location": "white_plains", "recurrence_kind": "weekly", "weekday": 2,
        "start_time": "09:00", "end_time": "12:00", "slot_minutes": 60})
    assert r.status_code == 201, r.text
    assert db.query(PelletAvailabilityTemplate).count() == 1
    assert db.query(PelletSlot).count() > 0


def test_list_and_delete_template(client, db):
    tid = client.post("/api/pellets/availability/templates", json={
        "location": "brandywine", "recurrence_kind": "daily",
        "start_time": "09:00", "end_time": "11:00", "slot_minutes": 60}).json()["id"]
    assert any(t["id"] == tid for t in client.get("/api/pellets/availability/templates").json()["items"])
    assert client.delete(f"/api/pellets/availability/templates/{tid}").status_code == 204
    # soft-disabled
    t = db.query(PelletAvailabilityTemplate).filter(PelletAvailabilityTemplate.id == tid).first()
    assert t.active is False


def test_add_adhoc_slot(client, db):
    r = client.post("/api/pellets/availability/slots", json={
        "location": "arlington", "slot_date": "2099-07-04",
        "start_time": "10:00", "end_time": "11:00"})
    assert r.status_code == 201, r.text
    s = db.query(PelletSlot).filter(PelletSlot.is_addon.is_(True)).first()
    assert s and s.location == "arlington"


def test_block_slot(client, db):
    s = PelletSlot(location="white_plains", slot_date=date(2099, 7, 1),
                   start_time=time(9), end_time=time(10), status="open")
    db.add(s); db.commit()
    r = client.post(f"/api/pellets/availability/slots/{s.id}/block")
    assert r.status_code == 200
    db.refresh(s); assert s.status == "blocked"


def test_materialize_endpoint(client, db):
    db.add(PelletAvailabilityTemplate(location="white_plains", recurrence_kind="daily",
                                      start_time=time(9), end_time=time(11),
                                      slot_minutes=60, active=True))
    db.commit()
    r = client.post("/api/pellets/availability/materialize")
    assert r.status_code == 200 and r.json()["created"] > 0
