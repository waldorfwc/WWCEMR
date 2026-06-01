"""Staff-side messaging endpoints + SMS notification."""
from unittest.mock import patch

from app.models.surgery import Surgery
from app.models.surgery_message import SurgeryMessage


def _seed_surgery(db, **kw):
    s = Surgery(chart_number=kw.get("chart","S1"),
                  patient_name=kw.get("name","Pat"),
                  status="new",
                  cell_phone=kw.get("phone","+12405551234"))
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_staff_messages_get_returns_thread_and_marks_read(client, db):
    s = _seed_surgery(db)
    db.add(SurgeryMessage(surgery_id=s.id, author_kind="patient",
                              body="When can I eat?"))
    db.commit()
    r = client.get(f"/api/staff/surgeries/{s.id}/messages")
    assert r.status_code == 200, r.text
    assert len(r.json()["messages"]) == 1
    rows = db.query(SurgeryMessage).filter(
        SurgeryMessage.surgery_id == s.id).all()
    assert all(m.read_by_staff_at is not None for m in rows
                  if m.author_kind == "patient")


def test_staff_messages_post_persists_and_sends_sms(client, db):
    s = _seed_surgery(db)
    db.commit()
    with patch("app.routers.surgery_messages.send_sms",
                return_value=True) as mock_sms:
        r = client.post(
            f"/api/staff/surgeries/{s.id}/messages",
            json={"body": "Clear liquids OK"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["author_kind"] == "staff"
    rows = db.query(SurgeryMessage).filter(
        SurgeryMessage.surgery_id == s.id).all()
    assert len(rows) == 1
    assert rows[0].body == "Clear liquids OK"
    assert rows[0].author_email == "tester@waldorfwomenscare.com"
    assert mock_sms.called
    sms_to, sms_body = mock_sms.call_args[0]
    assert sms_to == "+12405551234"
    assert "WWC" in sms_body or "wwc" in sms_body.lower()
    assert "gw.waldorfwomenscare.com" in sms_body


def test_staff_messages_post_soft_fails_on_sms_error(client, db):
    """If send_sms raises, the message should still be persisted."""
    s = _seed_surgery(db)
    db.commit()
    with patch("app.routers.surgery_messages.send_sms",
                side_effect=Exception("twilio down")):
        r = client.post(
            f"/api/staff/surgeries/{s.id}/messages",
            json={"body": "Hi"},
        )
    assert r.status_code == 200
    assert db.query(SurgeryMessage).count() == 1


def test_staff_messages_inbox_lists_surgeries_with_unread_patient_msgs(
        client, db):
    s1 = _seed_surgery(db, chart="A", name="Alice")
    s2 = _seed_surgery(db, chart="B", name="Bob")
    db.add(SurgeryMessage(surgery_id=s1.id, author_kind="patient",
                              body="hi"))
    db.add(SurgeryMessage(surgery_id=s2.id, author_kind="staff",
                              author_email="x@y", body="hi back"))
    db.commit()
    r = client.get("/api/staff/messages/inbox")
    assert r.status_code == 200
    rows = r.json()["rows"]
    sids = [r["surgery_id"] for r in rows]
    assert str(s1.id) in sids
    assert str(s2.id) not in sids


def test_staff_messages_inbox_drops_once_staff_views(client, db):
    s = _seed_surgery(db)
    db.add(SurgeryMessage(surgery_id=s.id, author_kind="patient",
                              body="?"))
    db.commit()
    assert any(r["surgery_id"] == str(s.id)
                  for r in client.get("/api/staff/messages/inbox").json()["rows"])
    client.get(f"/api/staff/surgeries/{s.id}/messages")
    assert not any(r["surgery_id"] == str(s.id)
                      for r in client.get("/api/staff/messages/inbox").json()["rows"])
