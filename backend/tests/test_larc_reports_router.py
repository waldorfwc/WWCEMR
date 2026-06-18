"""LARC Reports endpoints. `client` is the super-admin fixture."""
from datetime import datetime

from app.models.larc import LarcAssignment, LarcDeviceType


def _dtype(db, name="Liletta"):
    t = LarcDeviceType(name=name, category="larc"); db.add(t); db.commit(); db.refresh(t)
    return t


def _assign(db, t, **kw):
    base = dict(chart_number="M1", patient_name="Doe, J", status="inserted",
                source_flow="in_stock", device_type_id=t.id)
    base.update(kw)
    a = LarcAssignment(**base); db.add(a); db.commit(); db.refresh(a)
    return a


def test_summary_returns_all_tiles(client, db):
    t = _dtype(db)
    _assign(db, t, status="inserted", inserted_at=datetime(2026, 6, 10), billed_at=None)
    r = client.get("/api/larc/reports/summary?from=2026-06-01&to=2026-06-30")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("workflow_funnel", "outstanding_enrollment", "insertions",
                "billing_backlog", "owed_patients", "inventory_health",
                "insertion_outcomes", "period", "device_types"):
        assert key in body
    assert body["billing_backlog"]["count"] == 1


def test_rows_json_and_csv(client, db):
    t = _dtype(db)
    _assign(db, t, status="inserted", billed_at=None)
    j = client.get("/api/larc/reports/billing_backlog/rows")
    assert j.status_code == 200 and len(j.json()["items"]) == 1
    c = client.get("/api/larc/reports/billing_backlog/rows?format=csv")
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv")
    assert c.text.splitlines()[0].startswith("assignment_id")


def test_rows_unknown_tile_404(client, db):
    assert client.get("/api/larc/reports/bogus/rows").status_code == 404
