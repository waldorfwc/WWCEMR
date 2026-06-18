"""Authenticated walk-through of Pellet Recall: an overdue patient is
materialized, listed, opened (insertion history + caller script), and a call is
attempted + an outcome logged. `client` is the super-admin fixture."""
from datetime import timedelta

from app.models.pellet import PelletPatient, PelletVisit
from app.utils.dt import now_utc_naive


def test_pellet_recall_walkthrough(client, db, capsys):
    log = []
    p = PelletPatient(chart_number="WT-RECALL", patient_name="Roe, Pat", status="active",
                      patient_phone="3015550000", recall_interval_months=4)
    db.add(p); db.commit(); db.refresh(p)
    db.add(PelletVisit(patient_id=p.id, visit_kind="initial", status="billed",
                       location="white_plains", provider="Cooke, Aryian, MD",
                       inserted_at=now_utc_naive() - timedelta(days=210)))
    db.commit()

    # 1. Sync materializes the overdue patient into the recall engine.
    s = client.post("/api/pellets/recall/sync").json()
    assert s["created"] == 1
    log.append(f"1. POST /sync → {s}")

    # 2. The worklist lists them.
    items = client.get("/api/pellets/recall").json()["items"]
    assert len(items) == 1 and items[0]["chart_number"] == "WT-RECALL"
    rid = items[0]["id"]
    log.append(f"2. GET /pellets/recall → 1 due patient ({items[0]['patient_name']})")

    # 3. Detail shows insertion history + caller script + outcomes.
    body = client.get(f"/api/pellets/recall/{rid}").json()
    assert body["insertion_history"][0]["location"] == "white_plains"
    assert body["caller_script"] and body["outcomes"]
    log.append(f"3. GET /pellets/recall/{{id}} → insertion history "
               f"({body['insertion_history'][0]['date']} @ white_plains), caller script, "
               f"{len(body['outcomes'])} outcomes")

    # 4. Record an attempt (bumps attempts) then log the outcome (delegates).
    assert client.post(f"/api/pellets/recall/{rid}/call-attempted").status_code == 200
    r = client.post(f"/api/pellets/recall/{rid}/outcome",
                    json={"outcome": "Left voicemail", "notes": "left vm"})
    assert r.status_code == 200, r.text
    after = client.get(f"/api/pellets/recall/{rid}").json()
    assert after["recall"]["attempts"] >= 1
    assert any(h["outcome"] == "Left voicemail" for h in after["history"])
    log.append(f"4. POST /call-attempted + /outcome 'Left voicemail' → attempts "
               f"{after['recall']['attempts']}, logged in history")

    with capsys.disabled():
        print("\n  -- Pellet Recall walk-through (authenticated) --")
        for line in log:
            print("   " + line)
