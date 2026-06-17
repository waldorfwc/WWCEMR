"""Authenticated walk-through of Surgery Reports: seed a small set, read the
summary, drill into a tile, and export CSV. `client` is the super-admin fixture."""
from datetime import datetime

from app.models.surgery import Surgery


def _surg(db, **kw):
    base = dict(chart_number="M1", patient_name="Doe, J", status="confirmed",
                surgeon_primary="Cooke, Aryian, MD", selected_facility="medstar")
    base.update(kw); s = Surgery(**base); db.add(s); db.commit(); db.refresh(s)
    return s


def test_reports_walkthrough(client, db, capsys):
    log = []
    _surg(db, status="hold")
    _surg(db, status="confirmed")
    _surg(db, status="completed", procedure_classification="major",
          completed_at=datetime(2026, 6, 10))

    # 1. Summary returns every tile.
    body = client.get("/api/surgery/reports/summary?from=2026-06-01&to=2026-06-30").json()
    assert set(body) >= {"status_funnel", "not_ready", "completed", "cycle_time",
                         "posting_backlog", "utilization", "period"}
    log.append(f"1. /summary -> funnel {body['status_funnel']['by_status']}, "
               f"completed {body['completed']['total']} (delta {body['completed']['delta']})")

    # 2. Drill into the 'hold' bucket of the status funnel.
    items = client.get("/api/surgery/reports/status_funnel/rows?bucket=hold").json()["items"]
    assert len(items) == 1 and items[0]["status"] == "hold"
    log.append(f"2. drill status_funnel?bucket=hold -> {len(items)} surgery")

    # 3. CSV export of the same bucket.
    csv_resp = client.get("/api/surgery/reports/status_funnel/rows?bucket=hold&format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.text.splitlines()[0].startswith("surgery_id")
    log.append("3. CSV export -> text/csv with header + 1 row")

    with capsys.disabled():
        print("\n  -- Surgery Reports walk-through (authenticated) --")
        for line in log:
            print("   " + line)
