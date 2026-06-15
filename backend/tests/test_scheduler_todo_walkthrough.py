"""Authenticated walk-through of the Scheduler To-Do workspace: the live
step-derived Action-Needed queue (with auto-resolve), and the Recent Activity
feed (list -> unread count -> mark read -> read-all). Runs as the super-admin
test client — the same endpoints the To-Do page calls."""
from datetime import date, datetime, timedelta

from app.models.surgery import Surgery
from app.models.surgery_activity import SurgeryActivity
from app.services.surgery.activity import record_activity


def _surgery(db, **over):
    base = dict(
        chart_number="C900", patient_name="Demo, Pat",
        dob=date(1980, 1, 1), cell_phone="240-555-0900", email="d@x.c",
        address_street="1 St", address_city="Waldorf",
        address_state="MD", address_zip="20601",
        primary_insurance="Aetna", primary_member_id="M1",
        surgeon_primary="Dr. A",
        procedures=[{"cpt": "58571", "description": "TLH"}],
        diagnoses=[{"icd": "D25.9"}], estimated_minutes=180,
        eligible_facilities=["medstar"], selected_facility="medstar",
        preop_date=date(2026, 6, 1), auth_status="approved",
        status="in_progress", benefits_verified_at=None,
    )
    base.update(over)
    s = Surgery(**base)
    db.add(s); db.commit()
    return s


def test_scheduler_todo_walkthrough(client, db, capsys):
    log = []

    # ── Action Needed (live from the steps engine) ──
    behind = _surgery(db, chart_number="C-BEHIND", patient_name="Overdue, Olivia",
                      updated_at=datetime.utcnow() - timedelta(days=30),
                      created_at=datetime.utcnow() - timedelta(days=40))
    ontrack = _surgery(db, chart_number="C-OK", patient_name="Ontrack, Tina",
                       updated_at=datetime.utcnow(), created_at=datetime.utcnow())

    body = client.get("/api/surgery/todos").json()
    by_id = {it["surgery_id"]: it for it in body["items"]}
    assert by_id[str(behind.id)]["state"] == "behind"
    assert by_id[str(behind.id)]["days_behind"] > 0
    assert by_id[str(ontrack.id)]["state"] == "open"
    assert body["items"][0]["state"] == "behind"        # behind floats to top
    b = by_id[str(behind.id)]
    log.append(f"1. /todos: {body['open_count']} open · {body['behind_count']} behind")
    log.append(f"   top item (behind): {b['patient_name']} — step '{b['step_title']}' "
               f"({b['days_behind']}d behind)")
    log.append(f"   on-track item: {by_id[str(ontrack.id)]['patient_name']} — "
               f"step '{by_id[str(ontrack.id)]['step_title']}' (0d)")

    # ── Auto-resolve: complete the behind surgery's current step ──
    before_step = by_id[str(behind.id)]["step_key"]
    behind.benefits_verified_at = datetime.utcnow()      # finish the benefits step
    db.commit()
    after = {it["surgery_id"]: it for it in client.get("/api/surgery/todos").json()["items"]}
    after_step = after.get(str(behind.id), {}).get("step_key")
    assert after_step != before_step                     # the step advanced
    log.append(f"2. auto-resolve: completed step '{before_step}' → To-Do now shows "
               f"next step '{after_step}' (item moved on, no manual check-off)")

    # ── Recent Activity feed ──
    record_activity(db, ontrack, "date_picked",
                    "Patient picked a date: 07/01/2026 at MedStar")
    record_activity(db, ontrack, "consent_signed", "Consent signed (TLH)")
    record_activity(db, behind, "labs_reported", "Self-reported labs complete")
    db.commit()

    feed = client.get("/api/surgery/activity").json()
    rows = feed if isinstance(feed, list) else feed.get("items", feed.get("activity", []))
    assert len(rows) >= 3
    unread = client.get("/api/surgery/activity/unread-count").json()["count"]
    assert unread >= 3
    log.append(f"3. /activity feed: {len(rows)} events, newest first; "
               f"unread badge = {unread}")
    for r in rows[:3]:
        log.append(f"   • {r['summary']}  ({r.get('patient_name','')})")

    # ── Mark one read, then read-all ──
    first_id = rows[0]["id"]
    assert client.post(f"/api/surgery/activity/{first_id}/read").status_code in (200, 204)
    after_one = client.get("/api/surgery/activity/unread-count").json()["count"]
    assert after_one == unread - 1
    assert client.post("/api/surgery/activity/read-all").status_code in (200, 204)
    after_all = client.get("/api/surgery/activity/unread-count").json()["count"]
    assert after_all == 0
    log.append(f"4. mark one read → unread {unread}→{after_one}; "
               f"mark-all-read → unread {after_all}")

    with capsys.disabled():
        print("\n  ── scheduler to-do click-through (authenticated) ──")
        for line in log:
            print("   " + line)
