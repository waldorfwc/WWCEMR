"""Pellet Reports endpoints. `client` is the super-admin fixture."""
from datetime import datetime

from app.models.pellet import PelletPatient, PelletVisit


def _seed(db, **kw):
    # chart_number is unique — derive a fresh one per call.
    n = db.query(PelletPatient).count()
    p = PelletPatient(chart_number=f"PC{n + 1}", patient_name="Doe, J", status="active")
    db.add(p); db.commit(); db.refresh(p)
    base = dict(patient_id=p.id, visit_kind="initial", status="cancelled",
                location="white_plains", provider="Cooke, Aryian, MD")
    base.update(kw)
    v = PelletVisit(**base); db.add(v); db.commit(); db.refresh(v)
    return p, v


def test_summary_returns_all_tiles(client, db):
    _seed(db, status="cancelled")
    _seed(db, status="inserted", inserted_at=datetime(2026, 6, 10))
    r = client.get("/api/pellets/reports/summary?from=2026-06-01&to=2026-06-30")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("status_funnel", "insertions", "recall_due", "prerequisites",
                "billing_backlog", "inventory_health", "period", "providers"):
        assert key in body
    assert body["status_funnel"]["by_status"]["cancelled"] == 1


def test_rows_json_and_csv(client, db):
    _seed(db, status="cancelled")
    _seed(db, status="cancelled")
    j = client.get("/api/pellets/reports/status_funnel/rows?bucket=cancelled")
    assert j.status_code == 200 and len(j.json()["items"]) == 2
    c = client.get("/api/pellets/reports/status_funnel/rows?bucket=cancelled&format=csv")
    assert c.status_code == 200
    assert c.headers["content-type"].startswith("text/csv")
    assert c.text.splitlines()[0].startswith("visit_id")


def test_rows_unknown_tile_404(client, db):
    assert client.get("/api/pellets/reports/bogus/rows").status_code == 404
