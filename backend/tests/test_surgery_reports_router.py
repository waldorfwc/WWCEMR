"""Surgery Reports endpoints. `client` is the super-admin fixture."""
from datetime import datetime

from app.models.surgery import Surgery


def _surg(db, **kw):
    base = dict(chart_number="M1", patient_name="Doe, J", status="hold",
                surgeon_primary="Cooke, Aryian, MD", selected_facility="medstar")
    base.update(kw)
    s = Surgery(**base); db.add(s); db.commit(); db.refresh(s)
    return s


def test_summary_returns_all_tiles(client, db):
    _surg(db, status="hold")
    _surg(db, status="completed", procedure_classification="major",
          completed_at=datetime(2026, 6, 10))
    r = client.get("/api/surgery/reports/summary?from=2026-06-01&to=2026-06-30")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("status_funnel", "not_ready", "completed", "cycle_time",
                "posting_backlog", "utilization", "period"):
        assert key in body
    assert body["status_funnel"]["by_status"]["hold"] == 1


def test_rows_json_and_csv(client, db):
    _surg(db, status="hold")
    _surg(db, status="hold")
    j = client.get("/api/surgery/reports/status_funnel/rows?bucket=hold")
    assert j.status_code == 200 and len(j.json()["items"]) == 2
    c = client.get("/api/surgery/reports/status_funnel/rows?bucket=hold&format=csv")
    assert c.status_code == 200
    assert c.headers["content-type"].startswith("text/csv")
    assert c.text.splitlines()[0].startswith("surgery_id")


def test_rows_unknown_tile_404(client, db):
    assert client.get("/api/surgery/reports/bogus/rows").status_code == 404
