"""Tests for the fax status poller — state transitions based on RingCentral status."""
from datetime import datetime, timedelta
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.services.fax_poller import poll_outstanding_faxes


def test_poll_promotes_sent_to_delivered(db, monkeypatch):
    row = FaxLog(
        chart_number="PP1", doc_ids=["x"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="1", status=FaxLogStatus.SENT, ringcentral_message_id="rc-1",
        sent_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(row); db.commit(); db.refresh(row)

    def fake_check_fax_status(message_id):
        return {"status": "Sent"}  # RingCentral's final "Sent" = our "delivered"

    monkeypatch.setattr("app.services.fax_poller.check_fax_status", fake_check_fax_status)
    n = poll_outstanding_faxes(db)
    db.refresh(row)
    assert n >= 1
    assert row.status == FaxLogStatus.DELIVERED
    assert row.delivered_at is not None


def test_poll_marks_failed(db, monkeypatch):
    row = FaxLog(
        chart_number="PP2", doc_ids=["y"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="1", status=FaxLogStatus.SENT, ringcentral_message_id="rc-2",
        sent_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(row); db.commit(); db.refresh(row)
    monkeypatch.setattr("app.services.fax_poller.check_fax_status",
                        lambda mid: {"status": "SendingFailed", "error": "no answer"})
    poll_outstanding_faxes(db)
    db.refresh(row)
    assert row.status == FaxLogStatus.FAILED
    assert "no answer" in (row.error or "")


def test_poll_skips_terminal_rows(db, monkeypatch):
    row = FaxLog(
        chart_number="PP3", doc_ids=["z"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="1", status=FaxLogStatus.DELIVERED, ringcentral_message_id="rc-3",
        sent_at=datetime.utcnow() - timedelta(minutes=5),
        delivered_at=datetime.utcnow() - timedelta(minutes=4),
    )
    db.add(row); db.commit()

    called = {"n": 0}
    def never(mid):
        called["n"] += 1
        return {"status": "Sent"}
    monkeypatch.setattr("app.services.fax_poller.check_fax_status", never)
    poll_outstanding_faxes(db)
    assert called["n"] == 0


def test_poll_skips_rows_past_max_age(db, monkeypatch):
    row = FaxLog(
        chart_number="PP4", doc_ids=["z"], grouping_mode=GroupingMode.SEPARATE,
        dest_fax="1", status=FaxLogStatus.SENT, ringcentral_message_id="rc-4",
        sent_at=datetime.utcnow() - timedelta(hours=3),  # older than default 1h window
    )
    db.add(row); db.commit()

    called = {"n": 0}
    def never(mid):
        called["n"] += 1
        return {"status": "Sent"}
    monkeypatch.setattr("app.services.fax_poller.check_fax_status", never)
    poll_outstanding_faxes(db)
    assert called["n"] == 0
