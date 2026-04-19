"""GET /api/fax/by-chart/{chart_number} — fax history for the chart view chips."""
from datetime import datetime, timedelta
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode


def test_by_chart_returns_rows_for_that_chart_only(client, db):
    db.add_all([
        FaxLog(chart_number="AAAA", doc_ids=["d1"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT,
               sent_at=datetime.utcnow() - timedelta(minutes=1)),
        FaxLog(chart_number="AAAA", doc_ids=["d2", "d3"], grouping_mode=GroupingMode.COMBINED,
               dest_fax="1", status=FaxLogStatus.DELIVERED,
               sent_at=datetime.utcnow() - timedelta(minutes=2)),
        FaxLog(chart_number="BBBB", doc_ids=["d4"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT,
               sent_at=datetime.utcnow()),
    ])
    db.commit()

    r = client.get("/api/fax/by-chart/AAAA")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert {row["chart_number"] for row in rows} == {"AAAA"}

    # Shape check
    first = rows[0]
    assert set(first.keys()) >= {"id", "doc_ids", "status", "sent_at",
                                  "dest_fax", "grouping_mode", "error"}


def test_by_chart_empty_returns_empty_list(client, db):
    r = client.get("/api/fax/by-chart/NOPE")
    assert r.status_code == 200
    assert r.json() == []
