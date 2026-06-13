"""PATCH-ability of step-completion columns orphaned by the milestone→steps
cutover (Fable surgery audit #4, #5, #13, #14, #16).

Each new SurgeryPatch field must round-trip via GET *and* flip the matching
step in the serialized `steps` array, so the false "behind schedule" alert
clears once staff complete the step from the UI.
"""
from app.models.surgery import Surgery, SurgeryFile


def _seed(db, **kw):
    s = Surgery(chart_number="1", patient_name="Pat",
                eligible_facilities=["medstar"], selected_facility="medstar",
                status="in_progress",
                procedures=[{"name": "Hyst", "kind": "robotic_180"}],
                **kw)
    db.add(s)
    db.commit()
    return s


def _step_state(payload, key):
    for st in payload["steps"]:
        if st["key"] == key:
            return st["state"]
    return None


def test_labs_sent_flips_labs_step(client, db):
    s = _seed(db)
    r = client.get(f"/api/surgery/{s.id}")
    assert r.status_code == 200, r.text
    assert _step_state(r.json(), "labs") == "todo"

    r = client.patch(f"/api/surgery/{s.id}", json={"labs_sent_to_hospital": True})
    assert r.status_code == 200, r.text

    body = client.get(f"/api/surgery/{s.id}").json()
    assert body["labs_sent_to_hospital"] is True
    assert _step_state(body, "labs") == "done"


def test_post_op_call_flips_welfare_fu_step(client, db):
    s = _seed(db)
    assert _step_state(client.get(f"/api/surgery/{s.id}").json(), "welfare_fu") == "todo"

    # The step engine lowercases and compares == "spoke to pt."; the alert
    # bucket compares against the title-case "Spoke to Pt." — send the value
    # that satisfies both.
    r = client.patch(f"/api/surgery/{s.id}", json={"post_op_call_status": "Spoke to Pt."})
    assert r.status_code == 200, r.text

    body = client.get(f"/api/surgery/{s.id}").json()
    assert body["post_op_call_status"] == "Spoke to Pt."
    assert _step_state(body, "welfare_fu") == "done"


def test_operative_report_status_flips_notes_reports_step(client, db):
    s = _seed(db)
    assert _step_state(client.get(f"/api/surgery/{s.id}").json(), "notes_reports") == "todo"

    r = client.patch(f"/api/surgery/{s.id}", json={"operative_report_status": "completed"})
    assert r.status_code == 200, r.text

    body = client.get(f"/api/surgery/{s.id}").json()
    assert body["operative_report_status"] == "completed"
    assert _step_state(body, "notes_reports") == "done"

    # "received" is the other done state
    r = client.patch(f"/api/surgery/{s.id}", json={"operative_report_status": "received"})
    assert r.status_code == 200, r.text
    assert _step_state(client.get(f"/api/surgery/{s.id}").json(), "notes_reports") == "done"


def test_operative_report_status_rejects_garbage(client, db):
    s = _seed(db)
    r = client.patch(f"/api/surgery/{s.id}", json={"operative_report_status": "bogus"})
    assert r.status_code == 422, r.text
    # NULL would violate the NOT NULL column constraint
    r = client.patch(f"/api/surgery/{s.id}", json={"operative_report_status": None})
    assert r.status_code == 422, r.text


def test_boarding_slip_send_flips_post_to_hospital_step(client, db, monkeypatch):
    """#5 — sending the boarding slip is the act of posting to the hospital,
    so the post_to_hospital step (reads calendar_invite_sent_at) flips done."""
    s = _seed(db)
    db.add(SurgeryFile(surgery_id=s.id, kind="boarding_slip",
                       filename="bs.pdf", path="gs://bucket/bs.pdf",
                       mime_type="application/pdf"))
    db.commit()

    assert _step_state(client.get(f"/api/surgery/{s.id}").json(),
                       "post_to_hospital") == "todo"

    monkeypatch.setattr("app.services.storage.read_blob", lambda key: b"%PDF-1.4 fake")
    monkeypatch.setattr("app.services.fax_service.send_fax",
                        lambda **kw: {"message_id": "msg-123"})

    r = client.post(f"/api/surgery/{s.id}/boarding-slip/send",
                    json={"kind": "fax", "to": "2402522141"})
    assert r.status_code == 200, r.text

    body = client.get(f"/api/surgery/{s.id}").json()
    assert body["calendar_invite_sent_at"] is not None
    assert _step_state(body, "post_to_hospital") == "done"


def test_device_required_and_assigned_flip_device_step(client, db):
    s = _seed(db)
    # Not required → step is n/a
    assert _step_state(client.get(f"/api/surgery/{s.id}").json(), "device") == "n/a"

    r = client.patch(f"/api/surgery/{s.id}", json={"device_required": True})
    assert r.status_code == 200, r.text
    body = client.get(f"/api/surgery/{s.id}").json()
    assert body["device_required"] is True
    # Required but not assigned → todo
    assert _step_state(body, "device") == "todo"

    r = client.patch(f"/api/surgery/{s.id}", json={"device_assigned": True})
    assert r.status_code == 200, r.text
    body = client.get(f"/api/surgery/{s.id}").json()
    assert body["device_assigned"] is True
    assert _step_state(body, "device") == "done"
