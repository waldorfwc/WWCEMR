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


from app.models.document import PatientDocument
from datetime import date


def test_recent_includes_dob_doc_types_sent_by(client, db):
    db.merge(PatientDirectory(chart_number="DOBTEST", patient_name="Last, First", dob=date(1980, 5, 15)))
    # seed docs so doc_types can resolve
    doc1 = PatientDocument(chart_number="DOBTEST", doc_type="Insurance Card",
                           doc_id="I1", filename="a.pdf", file_path="/tmp/a.pdf")
    doc2 = PatientDocument(chart_number="DOBTEST", doc_type="Progress Note",
                           doc_id="P1", filename="b.pdf", file_path="/tmp/b.pdf")
    db.add_all([doc1, doc2])
    db.commit()
    db.refresh(doc1); db.refresh(doc2)

    db.add(FaxLog(
        chart_number="DOBTEST",
        doc_ids=[str(doc1.id), str(doc2.id)],
        grouping_mode=GroupingMode.COMBINED,
        dest_fax="2402522141",
        status=FaxLogStatus.SENT,
        sent_at=datetime.utcnow(),
        sent_by="tester@waldorfwomenscare.com",
    ))
    db.commit()

    r = client.get("/api/fax/recent")
    assert r.status_code == 200
    row = r.json()[0]
    assert row["dob"] == "1980-05-15"
    assert set(row["doc_types"]) == {"Insurance Card", "Progress Note"}
    assert row["sent_by"] == "tester@waldorfwomenscare.com"


def test_recent_window_filter(client, db):
    now = datetime.utcnow()
    db.add_all([
        FaxLog(chart_number="W1", doc_ids=["x"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT, sent_at=now - timedelta(days=3)),
        FaxLog(chart_number="W2", doc_ids=["y"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT, sent_at=now - timedelta(days=20)),
    ])
    db.commit()

    r_week = client.get("/api/fax/recent?window=7&limit=50")
    charts = {row["chart_number"] for row in r_week.json()}
    assert "W1" in charts
    assert "W2" not in charts

    r_month = client.get("/api/fax/recent?window=30&limit=50")
    charts = {row["chart_number"] for row in r_month.json()}
    assert charts == {"W1", "W2"}


def test_recent_status_filter(client, db):
    now = datetime.utcnow()
    db.add_all([
        FaxLog(chart_number="S1", doc_ids=["x"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.SENT, sent_at=now),
        FaxLog(chart_number="S2", doc_ids=["y"], grouping_mode=GroupingMode.SEPARATE,
               dest_fax="1", status=FaxLogStatus.FAILED, sent_at=now),
    ])
    db.commit()

    r = client.get("/api/fax/recent?status=failed&limit=50")
    charts = {row["chart_number"] for row in r.json()}
    assert charts == {"S2"}
