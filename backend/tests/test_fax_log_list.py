"""GET /api/fax-log — paginated fax listing with filters."""
from datetime import datetime, timedelta
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.models.patient_directory import PatientDirectory


def test_fax_log_pagination_filters(client, db):
    db.merge(PatientDirectory(chart_number="ZZ", patient_name="Z, P"))
    db.commit()
    base = datetime.utcnow()
    for i in range(12):
        db.add(FaxLog(
            chart_number="ZZ",
            doc_ids=["x"],
            grouping_mode=GroupingMode.SEPARATE,
            dest_fax="2402522141",
            status=FaxLogStatus.SENT if i % 2 == 0 else FaxLogStatus.FAILED,
            sent_at=base - timedelta(minutes=i),
        ))
    db.commit()

    # default page size 50 but cap at total
    r1 = client.get("/api/fax-log")
    body1 = r1.json()
    assert body1["total"] == 12
    assert len(body1["rows"]) == 12
    assert body1["page"] == 1

    # page size and paging
    r2 = client.get("/api/fax-log?page_size=5&page=2")
    body2 = r2.json()
    assert len(body2["rows"]) == 5
    assert body2["page"] == 2
    assert body2["total"] == 12

    # filter by status
    r3 = client.get("/api/fax-log?status=failed")
    assert all(r["status"] == "failed" for r in r3.json()["rows"])
    assert r3.json()["total"] == 6

    # filter by chart
    r4 = client.get("/api/fax-log?chart=ZZ")
    assert r4.json()["total"] == 12
    r5 = client.get("/api/fax-log?chart=NOPE")
    assert r5.json()["total"] == 0
