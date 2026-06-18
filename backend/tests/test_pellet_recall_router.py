"""Pellet recall endpoints. `client` is the super-admin fixture."""
from datetime import timedelta

from app.models.pellet import PelletPatient, PelletVisit
from app.models.recall import RecallEntry
from app.services.pellet.recall_sync import PELLET_RECALL_TYPE
from app.utils.dt import now_utc_naive


def _due(db, chart="DUE1"):
    p = PelletPatient(chart_number=chart, patient_name=f"Pt {chart}", status="active",
                      patient_phone="3015551234", recall_interval_months=4)
    db.add(p); db.commit(); db.refresh(p)
    db.add(PelletVisit(patient_id=p.id, visit_kind="initial", status="billed",
                       location="white_plains", provider="Cooke, Aryian, MD",
                       inserted_at=now_utc_naive() - timedelta(days=200)))
    db.commit()
    return p


def test_sync_then_list(client, db):
    _due(db)
    assert client.post("/api/pellets/recall/sync").status_code == 200
    items = client.get("/api/pellets/recall").json()["items"]
    assert len(items) == 1 and items[0]["chart_number"] == "DUE1"


def test_detail_has_insertion_history_and_script(client, db):
    _due(db, "DUE2")
    client.post("/api/pellets/recall/sync")
    rid = client.get("/api/pellets/recall").json()["items"][0]["id"]
    body = client.get(f"/api/pellets/recall/{rid}").json()
    assert body["recall"]["chart_number"] == "DUE2"
    assert len(body["insertion_history"]) == 1
    assert body["insertion_history"][0]["location"] == "white_plains"
    assert body["caller_script"] and "outcomes" in body
    assert "{months}" not in body["caller_script"]   # placeholder substituted
    assert any(h["event_type"] == "detail_viewed" for h in body["history"])


def test_detail_404_for_non_pellet_entry(client, db):
    e = RecallEntry(chart_number="WWE9", recall_type="Est - Well-Woman Exam",
                    source="smartsheet", status="active")
    db.add(e); db.commit(); db.refresh(e)
    assert client.get(f"/api/pellets/recall/{e.id}").status_code == 404


def test_claim_and_outcome_delegate(client, db):
    _due(db, "ACT1")
    client.post("/api/pellets/recall/sync")
    rid = client.get("/api/pellets/recall").json()["items"][0]["id"]
    assert client.post(f"/api/pellets/recall/{rid}/claim").status_code == 200
    # Realistic flow: record the attempt (bumps attempts via the engine), then
    # log the outcome. The outcome endpoint is a pure delegation, same as WWE.
    assert client.post(f"/api/pellets/recall/{rid}/call-attempted").status_code == 200
    r = client.post(f"/api/pellets/recall/{rid}/outcome",
                    json={"outcome": "Left voicemail", "notes": "vm 1"})
    assert r.status_code == 200, r.text
    body = client.get(f"/api/pellets/recall/{rid}").json()
    assert body["recall"]["attempts"] >= 1                 # from call-attempted
    assert any(h["outcome"] == "Left voicemail" for h in body["history"])


def test_action_404_on_non_pellet_entry(client, db):
    from app.models.recall import RecallEntry
    e = RecallEntry(chart_number="WWE8", recall_type="Est - Well-Woman Exam",
                    source="smartsheet", status="active")
    db.add(e); db.commit(); db.refresh(e)
    assert client.post(f"/api/pellets/recall/{e.id}/claim").status_code == 404
    assert client.post(f"/api/pellets/recall/{e.id}/outcome",
                       json={"outcome": "Scheduled"}).status_code == 404
