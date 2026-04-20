"""GET /api/fax/chart-summary — per-chart fax-count and last-sent-date map."""
from datetime import datetime, timedelta
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode


def test_chart_summary_groups_by_chart(client, db):
    now = datetime.utcnow()
    db.add_all([
        FaxLog(chart_number="AAA", doc_ids=["d1"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT, sent_at=now - timedelta(hours=1)),
        FaxLog(chart_number="AAA", doc_ids=["d2"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.DELIVERED, sent_at=now - timedelta(minutes=5)),
        FaxLog(chart_number="BBB", doc_ids=["d3"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT, sent_at=now - timedelta(days=2)),
    ])
    db.commit()

    r = client.get("/api/fax/chart-summary")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    # 2 charts
    assert len(body) == 2

    by_chart = {row["chart_number"]: row for row in body}
    assert by_chart["AAA"]["fax_count"] == 2
    assert by_chart["BBB"]["fax_count"] == 1
    # last_sent_at is the max sent_at for the chart — AAA's newer row
    assert by_chart["AAA"]["last_sent_at"] > by_chart["BBB"]["last_sent_at"]


def test_chart_summary_empty_db_returns_empty_list(client, db):
    r = client.get("/api/fax/chart-summary")
    assert r.status_code == 200
    assert r.json() == []
