"""Authenticated walk-through of Pellet Reports: seed a small set, read the
summary, drill into a tile, and export CSV. `client` is the super-admin fixture."""
from datetime import datetime

from app.models.pellet import PelletPatient, PelletVisit


def _seed(db, **kw):
    p = PelletPatient(chart_number=f"PC-{kw.get('status','x')}",
                      patient_name="Roe, Pat", status="active")
    db.add(p); db.commit(); db.refresh(p)
    base = dict(patient_id=p.id, visit_kind="initial", status="new",
                location="white_plains", provider="Cooke, Aryian, MD")
    base.update(kw)
    v = PelletVisit(**base); db.add(v); db.commit(); db.refresh(v)
    return p, v


def test_pellet_reports_walkthrough(client, db, capsys):
    log = []
    _seed(db, status="cancelled")
    _seed(db, status="new")
    _seed(db, status="inserted", visit_kind="booster", inserted_at=datetime(2026, 6, 10))

    body = client.get("/api/pellets/reports/summary?from=2026-06-01&to=2026-06-30").json()
    assert set(body) >= {"status_funnel", "insertions", "recall_due", "prerequisites",
                         "billing_backlog", "inventory_health", "period", "providers"}
    log.append(f"1. /summary -> funnel {body['status_funnel']['by_status']}, "
               f"insertions {body['insertions']['total']} (delta {body['insertions']['delta']})")

    items = client.get("/api/pellets/reports/status_funnel/rows?bucket=cancelled").json()["items"]
    assert len(items) == 1 and items[0]["status"] == "cancelled"
    log.append(f"2. drill status_funnel?bucket=cancelled -> {len(items)} visit")

    csv_resp = client.get("/api/pellets/reports/status_funnel/rows?bucket=cancelled&format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.text.splitlines()[0].startswith("visit_id")
    log.append("3. CSV export -> text/csv with header + 1 row")

    with capsys.disabled():
        print("\n  -- Pellet Reports walk-through (authenticated) --")
        for line in log:
            print("   " + line)
