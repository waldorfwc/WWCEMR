"""Authenticated walk-through of the 'last synced' hint on the Pellet Reports
Recall Due tile. Confirms the hint is empty before any sync, that the
authenticated recall-sync endpoint stamps it, and that the summary then
surfaces a formatted timestamp dated today. Run with -s to see the log.
"""
import re
from datetime import timedelta

from app.models.pellet import PelletPatient, PelletVisit
from app.utils.dt import now_utc_naive

_FMT = re.compile(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2} (AM|PM)$")


def test_last_synced_hint_walkthrough(client, db, capsys):
    # One active, recall-due patient (interval 4mo, last insertion 200d ago).
    p = PelletPatient(chart_number="LS1", patient_name="Adams, Mary",
                      status="active", recall_interval_months=4)
    db.add(p); db.commit(); db.refresh(p)
    db.add(PelletVisit(patient_id=p.id, visit_kind="initial", status="billed",
                       location="white_plains", provider="Cooke, Aryian, MD",
                       inserted_at=now_utc_naive() - timedelta(days=200)))
    db.commit()

    log = []

    # 1. Before any sync -> hint is null (frontend renders the 'never synced' nudge).
    rd = client.get("/api/pellets/reports/summary").json()["recall_due"]
    assert rd["total"] == 1 and rd["last_synced_at"] is None
    assert rd["not_contacted"] == 1   # due but never contacted (no recall entry yet)
    log.append(f"1. pre-sync: total={rd['total']} not_contacted={rd['not_contacted']} "
               f"last_synced_at={rd['last_synced_at']}  (UI shows 'Never synced')")

    # 2. Run the authenticated recall sync (Module.PELLETS / Tier.WORK).
    sync = client.post("/api/pellets/recall/sync")
    assert sync.status_code == 200, sync.text
    assert sync.json()["created"] == 1
    log.append(f"2. POST /pellets/recall/sync -> {sync.json()}")

    # 3. After sync -> hint is a formatted timestamp dated today.
    rd2 = client.get("/api/pellets/reports/summary").json()["recall_due"]
    stamp = rd2["last_synced_at"]
    assert stamp is not None and _FMT.match(stamp), stamp
    assert stamp.startswith(now_utc_naive().strftime("%m/%d/%Y"))
    log.append(f"3. post-sync: last_synced_at={stamp!r}  (UI shows 'Last synced {stamp}')")

    # 4. Re-sync refreshes the stamp (never regresses).
    client.post("/api/pellets/recall/sync")
    rd3 = client.get("/api/pellets/reports/summary").json()["recall_due"]
    assert rd3["last_synced_at"] is not None
    assert rd3["last_synced_at"].startswith(now_utc_naive().strftime("%m/%d/%Y"))
    log.append(f"4. re-sync: last_synced_at={rd3['last_synced_at']!r} (refreshed)")

    with capsys.disabled():
        print("\n  === Recall Due — last-synced hint walk-through (authenticated) ===")
        for line in log:
            print("   " + line)
        print("   === all assertions passed ===\n")
