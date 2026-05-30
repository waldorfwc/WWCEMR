"""Coverage for the extended waitlist payload (Phase A)."""
from datetime import date

from app.models.surgery import Surgery, SurgeryWaitlist


def _make_surgery(db, **kw):
    s = Surgery(
        chart_number="1234",
        patient_name="Jane Doe",
        procedures=[{"name": "Hysterectomy", "cpt": "58150"}],
        eligible_facilities=["medstar", "office"],
        selected_facility="medstar",
        urgency=kw.pop("urgency", "routine"),
        status="in_progress",
    )
    for k, v in kw.items():
        setattr(s, k, v)
    db.add(s); db.flush()
    return s


def test_waitlist_returns_new_columns(client, db):
    s = _make_surgery(db, urgency="urgent")
    db.add(SurgeryWaitlist(surgery_id=s.id, advance_notice_days=10))
    db.commit()

    resp = client.get("/api/surgery/admin/waitlist")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["waitlist"]
    assert len(rows) == 1
    row = rows[0]
    assert row["patient_name"] == "Jane Doe"
    assert row["advance_notice_days"] == 10
    assert row["procedure_name"] == "Hysterectomy"
    assert row["facility"] == "medstar"
    assert row["urgency"] == "urgent"


def test_waitlist_facility_falls_back_to_first_eligible(client, db):
    s = _make_surgery(db, selected_facility=None,
                       eligible_facilities=["office", "crmc"])
    db.add(SurgeryWaitlist(surgery_id=s.id, advance_notice_days=5))
    db.commit()

    rows = client.get("/api/surgery/admin/waitlist").json()["waitlist"]
    assert rows[0]["facility"] == "office"


def test_patch_surgery_accepts_urgency(client, db):
    s = _make_surgery(db, urgency="routine")
    db.commit()

    resp = client.patch(f"/api/surgery/{s.id}", json={"urgency": "expedited"})
    assert resp.status_code == 200, resp.text
    db.refresh(s)
    assert s.urgency == "expedited"


def test_patch_surgery_rejects_bogus_urgency(client, db):
    s = _make_surgery(db)
    db.commit()

    resp = client.patch(f"/api/surgery/{s.id}", json={"urgency": "panic"})
    assert resp.status_code == 422


def test_waitlist_handles_empty_procedures(client, db):
    s = _make_surgery(db, procedures=[])
    db.add(SurgeryWaitlist(surgery_id=s.id, advance_notice_days=3))
    db.commit()

    rows = client.get("/api/surgery/admin/waitlist").json()["waitlist"]
    assert rows[0]["procedure_name"] is None


def test_waitlist_handles_no_facility_anywhere(client, db):
    s = _make_surgery(db, selected_facility=None, eligible_facilities=[])
    db.add(SurgeryWaitlist(surgery_id=s.id, advance_notice_days=3))
    db.commit()

    rows = client.get("/api/surgery/admin/waitlist").json()["waitlist"]
    assert rows[0]["facility"] is None
