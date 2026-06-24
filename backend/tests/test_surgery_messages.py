"""Staff-side messaging endpoints + SMS notification."""
from unittest.mock import patch

from app.models.surgery import Surgery
from app.models.surgery_message import SurgeryMessage


def _seed_surgery(db, **kw):
    s = Surgery(chart_number=kw.get("chart","S1"),
                  patient_name=kw.get("name","Pat"),
                  status="new",
                  sms_consent=True,
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
    # GET no longer marks read as a side effect (Fable M3); marking read is
    # now an explicit POST so prefetch/auto-reload can't silently clear the
    # shared inbox.
    mr = client.post(f"/api/staff/surgeries/{s.id}/messages/mark-read")
    assert mr.status_code == 200, mr.text
    db.expire_all()
    rows = db.query(SurgeryMessage).filter(
        SurgeryMessage.surgery_id == s.id).all()
    assert all(m.read_by_staff_at is not None for m in rows
                  if m.author_kind == "patient")


def test_staff_messages_post_persists_and_sends_sms(client, db):
    s = _seed_surgery(db)
    db.commit()
    # The router now routes the patient-notification SMS through
    # send_patient_sms (consent gate + PatientSms audit row); the Twilio
    # seam it ultimately calls is send_sms in app.services.patient_sms.
    with patch("app.services.patient_sms.send_sms",
                return_value="SM123") as mock_sms:
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
    """If the Twilio send raises, the message should still be persisted."""
    s = _seed_surgery(db)
    db.commit()
    with patch("app.services.patient_sms.send_sms",
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
    # Marking read is now an explicit POST (GET has no side effect, Fable M3).
    client.post(f"/api/staff/surgeries/{s.id}/messages/mark-read")
    assert not any(r["surgery_id"] == str(s.id)
                      for r in client.get("/api/staff/messages/inbox").json()["rows"])


def test_staff_thread_payload_exposes_read_by_staff_at(client, db):
    """The frontend gates its mark-read POST on this field, so the thread
    payload must expose it (null until read, set after)."""
    s = _seed_surgery(db)
    db.add(SurgeryMessage(surgery_id=s.id, author_kind="patient", body="q"))
    db.commit()
    msgs = client.get(f"/api/staff/surgeries/{s.id}/messages").json()["messages"]
    assert "read_by_staff_at" in msgs[0]
    assert msgs[0]["read_by_staff_at"] is None
    client.post(f"/api/staff/surgeries/{s.id}/messages/mark-read")
    msgs = client.get(f"/api/staff/surgeries/{s.id}/messages").json()["messages"]
    assert msgs[0]["read_by_staff_at"] is not None


def test_staff_thread_reports_notify_status(client, db):
    # consent + phone -> can notify
    ok = _seed_surgery(db, chart="OK", name="Okay", phone="+12405550000")
    ok.sms_consent = True
    db.commit()
    body = client.get(f"/api/staff/surgeries/{ok.id}/messages").json()
    assert body["can_notify"] is True and body["notify_block"] is None

    # consent off -> blocked: no_consent
    nc = _seed_surgery(db, chart="NC", name="NoConsent", phone="+12405550001")
    nc.sms_consent = False
    db.commit()
    body = client.get(f"/api/staff/surgeries/{nc.id}/messages").json()
    assert body["can_notify"] is False and body["notify_block"] == "no_consent"

    # consent on but no phone -> blocked: no_phone
    np = _seed_surgery(db, chart="NP", name="NoPhone", phone="")
    np.sms_consent = True
    db.commit()
    body = client.get(f"/api/staff/surgeries/{np.id}/messages").json()
    assert body["can_notify"] is False and body["notify_block"] == "no_phone"


def test_staff_inbox_read_view_lists_only_fully_read_threads(client, db):
    s_unread = _seed_surgery(db, chart="U", name="Unreadly")
    s_read = _seed_surgery(db, chart="R", name="Readly")
    db.add(SurgeryMessage(surgery_id=s_unread.id, author_kind="patient", body="hi"))
    db.add(SurgeryMessage(surgery_id=s_read.id, author_kind="patient", body="hi"))
    db.commit()
    client.post(f"/api/staff/surgeries/{s_read.id}/messages/mark-read")

    read_rows = client.get("/api/staff/messages/inbox?view=read").json()["rows"]
    read_sids = [r["surgery_id"] for r in read_rows]
    assert str(s_read.id) in read_sids       # fully-read thread shows under "read"
    assert str(s_unread.id) not in read_sids  # still-unread thread does NOT

    unread_rows = client.get("/api/staff/messages/inbox?view=unread").json()["rows"]
    unread_sids = [r["surgery_id"] for r in unread_rows]
    assert str(s_unread.id) in unread_sids
    assert str(s_read.id) not in unread_sids


def test_staff_inbox_read_view_excludes_thread_with_any_unread(client, db):
    """A thread with one read + one unread patient message is NOT 'read'."""
    s = _seed_surgery(db)
    db.add(SurgeryMessage(surgery_id=s.id, author_kind="patient", body="first"))
    db.commit()
    client.post(f"/api/staff/surgeries/{s.id}/messages/mark-read")
    db.add(SurgeryMessage(surgery_id=s.id, author_kind="patient", body="second"))
    db.commit()
    read_sids = [r["surgery_id"]
                 for r in client.get("/api/staff/messages/inbox?view=read").json()["rows"]]
    assert str(s.id) not in read_sids
    unread_sids = [r["surgery_id"]
                   for r in client.get("/api/staff/messages/inbox?view=unread").json()["rows"]]
    assert str(s.id) in unread_sids


def test_staff_inbox_search_filters_by_name_or_chart(client, db):
    s1 = _seed_surgery(db, chart="C100", name="Alice Adams")
    s2 = _seed_surgery(db, chart="C200", name="Bob Brown")
    db.add(SurgeryMessage(surgery_id=s1.id, author_kind="patient", body="hi"))
    db.add(SurgeryMessage(surgery_id=s2.id, author_kind="patient", body="hi"))
    db.commit()
    by_name = client.get("/api/staff/messages/inbox?q=alice").json()["rows"]
    assert [r["surgery_id"] for r in by_name] == [str(s1.id)]
    by_chart = client.get("/api/staff/messages/inbox?q=C200").json()["rows"]
    assert [r["surgery_id"] for r in by_chart] == [str(s2.id)]


def test_staff_inbox_rejects_bad_view(client, db):
    assert client.get("/api/staff/messages/inbox?view=bogus").status_code == 422
