"""SurgeryActivity model + helper (B1), patient-action parity (B2),
/surgery/todos (B3) and the activity feed endpoints (B4)."""
from datetime import date, datetime, timedelta

from app.models.surgery import Surgery
from app.models.surgery_activity import SurgeryActivity
from app.services.surgery.activity import record_activity


def _surgery(db, **over):
    """A complete-info hospital surgery whose current step is `benefits`."""
    base = dict(
        chart_number="C200", patient_name="Activity, Pat",
        dob=date(1980, 1, 1), cell_phone="240-555-0200", email="a@x.c",
        address_street="1 St", address_city="Waldorf",
        address_state="MD", address_zip="20601",
        primary_insurance="Aetna", primary_member_id="M1",
        surgeon_primary="Dr. A",
        procedures=[{"cpt": "58571", "description": "TLH"}],
        diagnoses=[{"icd": "D25.9"}], estimated_minutes=180,
        eligible_facilities=["medstar"], selected_facility="medstar",
        preop_date=date(2026, 6, 1), auth_status="approved",
        status="in_progress",
        benefits_verified_at=None,
    )
    base.update(over)
    s = Surgery(**base)
    db.add(s)
    db.commit()
    return s


def test_record_activity_inserts_a_row(db):
    s = _surgery(db)
    record_activity(db, s, "date_picked",
                    "Patient picked a date: 07/01/2026 at medstar")
    db.commit()

    rows = db.query(SurgeryActivity).filter(
        SurgeryActivity.surgery_id == s.id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "date_picked"
    assert row.actor == "patient"          # default
    assert row.read_at is None
    assert "picked a date" in row.summary


def test_record_activity_soft_fails(db):
    """A bad row must not raise into the caller."""
    class Boom:
        id = "nope"

    record_activity(db, Boom(), "date_picked", "x")


# ─── B2 parity ──────────────────────────────────────────────────────

def test_labs_self_report_logs_activity(client, db):
    """Hitting the patient labs self-report endpoint creates a
    labs_reported activity row (alongside the existing flag flip)."""
    from app.services.patient_portal_auth import issue_portal_token
    s = _surgery(db)
    token = issue_portal_token(s)
    r = client.post(f"/api/patient/portal/{s.id}/self-report/labs",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    rows = db.query(SurgeryActivity).filter(
        SurgeryActivity.surgery_id == s.id,
        SurgeryActivity.kind == "labs_reported").all()
    assert len(rows) == 1
    assert rows[0].actor == "patient"


# ─── B3 /surgery/todos ──────────────────────────────────────────────

def test_todos_behind_and_open(client, db):
    # Behind: benefits step entered weeks ago.
    behind = _surgery(
        db, chart_number="C-BEHIND",
        updated_at=datetime.utcnow() - timedelta(days=30),
        created_at=datetime.utcnow() - timedelta(days=40))
    # On-track: same shape but freshly updated.
    ontrack = _surgery(
        db, chart_number="C-OPEN",
        updated_at=datetime.utcnow(), created_at=datetime.utcnow())

    r = client.get("/api/surgery/todos")
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {it["surgery_id"]: it for it in body["items"]}

    assert by_id[str(behind.id)]["state"] == "behind"
    assert by_id[str(behind.id)]["days_behind"] > 0
    assert by_id[str(ontrack.id)]["state"] == "open"
    assert by_id[str(ontrack.id)]["days_behind"] == 0
    assert body["behind_count"] >= 1
    assert body["open_count"] >= 1
    # Behind items float to the top.
    assert body["items"][0]["state"] == "behind"


def test_todos_behind_only_filter(client, db):
    behind = _surgery(
        db, chart_number="C-B2",
        updated_at=datetime.utcnow() - timedelta(days=30),
        created_at=datetime.utcnow() - timedelta(days=40))
    _surgery(db, chart_number="C-O2", updated_at=datetime.utcnow(),
             created_at=datetime.utcnow())

    r = client.get("/api/surgery/todos?behind_only=true")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items                                    # not empty
    assert all(it["state"] == "behind" for it in items)
    assert str(behind.id) in {it["surgery_id"] for it in items}


# ─── B4 activity feed endpoints ─────────────────────────────────────

def test_activity_list_newest_first(client, db):
    s = _surgery(db)
    older = SurgeryActivity(surgery_id=s.id, kind="date_picked",
                            summary="old", actor="patient",
                            created_at=datetime.utcnow() - timedelta(hours=2))
    newer = SurgeryActivity(surgery_id=s.id, kind="payment_made",
                            summary="new", actor="patient",
                            created_at=datetime.utcnow())
    db.add_all([older, newer])
    db.commit()

    r = client.get("/api/surgery/activity")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["summary"] == "new"
    assert items[0]["patient_name"] == s.patient_name
    assert items[0]["chart_number"] == s.chart_number


def test_activity_excludes_soft_deleted_surgery(client, db):
    s = _surgery(db)
    db.add(SurgeryActivity(surgery_id=s.id, kind="payment_made",
                           summary="x", actor="patient"))
    db.commit()
    s.soft_delete("admin@x.c")
    db.commit()

    items = client.get("/api/surgery/activity").json()["items"]
    assert str(s.id) not in {it["surgery_id"] for it in items}


def test_activity_unread_count_and_mark_read(client, db):
    s = _surgery(db)
    a = SurgeryActivity(surgery_id=s.id, kind="payment_made",
                        summary="x", actor="patient")
    b = SurgeryActivity(surgery_id=s.id, kind="date_picked",
                        summary="y", actor="patient")
    db.add_all([a, b])
    db.commit()

    assert client.get(
        "/api/surgery/activity/unread-count").json()["count"] == 2

    r = client.post(f"/api/surgery/activity/{a.id}/read")
    assert r.status_code == 200
    assert client.get(
        "/api/surgery/activity/unread-count").json()["count"] == 1

    r = client.post("/api/surgery/activity/read-all")
    assert r.status_code == 200
    assert client.get(
        "/api/surgery/activity/unread-count").json()["count"] == 0


def test_activity_unread_only_filter(client, db):
    s = _surgery(db)
    read_row = SurgeryActivity(surgery_id=s.id, kind="payment_made",
                               summary="read", actor="patient",
                               read_at=datetime.utcnow(), read_by="me@x.c")
    unread_row = SurgeryActivity(surgery_id=s.id, kind="date_picked",
                                 summary="unread", actor="patient")
    db.add_all([read_row, unread_row])
    db.commit()

    items = client.get(
        "/api/surgery/activity?unread_only=true").json()["items"]
    summaries = {it["summary"] for it in items}
    assert "unread" in summaries
    assert "read" not in summaries


def test_todos_includes_incomplete_for_review(client, db):
    inc = _surgery(db, chart_number="C-INC", status="incomplete")
    body = client.get("/api/surgery/todos").json()
    item = next((it for it in body["items"] if it["surgery_id"] == str(inc.id)), None)
    assert item is not None
    assert item["state"] == "incomplete"
    assert item["step_title"] == "Review & complete intake"
    assert body["incomplete_count"] >= 1
    # behind_only hides intake-review items
    bo = client.get("/api/surgery/todos?behind_only=true").json()
    assert str(inc.id) not in {it["surgery_id"] for it in bo["items"]}
