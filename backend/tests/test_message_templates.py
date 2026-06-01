"""Message templates — CRUD + rendered output."""
from app.models.surgery import Surgery
from app.models.surgery_message import MessageTemplate


def test_message_templates_list_empty(client, db):
    r = client.get("/api/staff/message-templates")
    assert r.status_code == 200
    assert r.json() == {"templates": []}


def test_message_templates_crud_round_trip(client, db):
    r = client.post("/api/staff/message-templates",
                       json={"name": "Test", "body": "Hi {{patient_name}}"})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    r = client.get("/api/staff/message-templates")
    assert len(r.json()["templates"]) == 1
    r = client.put(f"/api/staff/message-templates/{tid}",
                      json={"name": "Test edited", "body": "Hi {{patient_name}}!"})
    assert r.status_code == 200
    assert r.json()["name"] == "Test edited"
    r = client.delete(f"/api/staff/message-templates/{tid}")
    assert r.status_code == 200
    assert db.query(MessageTemplate).count() == 0


def test_message_templates_render_substitutes_patient_and_date(client, db):
    from datetime import date
    s = Surgery(chart_number="1", patient_name="Jane Doe", status="new",
                  scheduled_date=date(2026, 6, 15))
    db.add(s); db.commit(); db.refresh(s)
    t = MessageTemplate(name="Hi", body="Hello {{patient_name}}, "
                                          "your date is {{surgery_date}}.")
    db.add(t); db.commit(); db.refresh(t)
    r = client.get(
        f"/api/staff/message-templates/{t.id}/render",
        params={"surgery_id": str(s.id)},
    )
    assert r.status_code == 200
    rendered = r.json()["body"]
    assert "Jane Doe" in rendered
    assert "June 15, 2026" in rendered


def test_message_templates_render_handles_missing_scheduled_date(client, db):
    s = Surgery(chart_number="2", patient_name="Pat", status="new",
                  scheduled_date=None)
    db.add(s); db.commit(); db.refresh(s)
    t = MessageTemplate(name="X", body="Date: {{surgery_date}}")
    db.add(t); db.commit(); db.refresh(t)
    r = client.get(
        f"/api/staff/message-templates/{t.id}/render",
        params={"surgery_id": str(s.id)},
    )
    assert r.status_code == 200
    assert r.json()["body"] == "Date: "
