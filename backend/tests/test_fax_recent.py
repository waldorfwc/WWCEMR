"""GET /api/fax/recent — recent fax activity for the Dashboard card."""
from datetime import datetime, timedelta
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.models.patient_directory import PatientDirectory


def test_recent_returns_latest_first(client, db):
    db.merge(PatientDirectory(chart_number="77777", patient_name="Adams, Pamella"))
    db.merge(PatientDirectory(chart_number="88888", patient_name="Carter, Janice"))
    db.commit()

    older = FaxLog(
        chart_number="77777", doc_ids=["a"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="2402522141", status=FaxLogStatus.DELIVERED,
        sent_at=datetime.utcnow() - timedelta(hours=3),
    )
    newer = FaxLog(
        chart_number="88888", doc_ids=["b", "c"], grouping_mode=GroupingMode.COMBINED,
        dest_fax="2402522141", status=FaxLogStatus.SENT,
        sent_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add_all([older, newer])
    db.commit()

    r = client.get("/api/fax/recent?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 2
    # newer first
    assert body[0]["chart_number"] == "88888"
    assert body[0]["patient_name"] == "Carter, Janice"
    assert body[0]["doc_count"] == 2
    assert body[0]["status"] == "sent"
    assert body[1]["chart_number"] == "77777"
    assert body[1]["doc_count"] == 1


def test_recent_defaults_to_5_respects_limit(client, db):
    for i in range(7):
        db.add(FaxLog(
            chart_number=f"C{i}", doc_ids=["x"], grouping_mode=GroupingMode.SEPARATE,
            dest_fax="1", status=FaxLogStatus.SENT,
            sent_at=datetime.utcnow() - timedelta(minutes=i),
        ))
    db.commit()

    assert len(client.get("/api/fax/recent").json()) == 5
    assert len(client.get("/api/fax/recent?limit=3").json()) == 3
    assert len(client.get("/api/fax/recent?limit=100").json()) == 7


def test_recent_handles_missing_patient(client, db):
    db.add(FaxLog(
        chart_number="UNKNOWN", doc_ids=["z"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="1", status=FaxLogStatus.SENT, sent_at=datetime.utcnow(),
    ))
    db.commit()
    r = client.get("/api/fax/recent")
    assert r.status_code == 200
    assert r.json()[0]["patient_name"] == "UNKNOWN"  # falls back to chart number
