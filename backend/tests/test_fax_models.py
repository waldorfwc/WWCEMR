"""Tests for FaxLog and PracticeConfig model creation + basic queries."""
from datetime import datetime
from app.models.fax_log import FaxLog, FaxLogStatus, GroupingMode
from app.models.practice_config import PracticeConfig, get_setting


def test_fax_log_defaults(db):
    row = FaxLog(
        chart_number="12345",
        doc_ids=["11111111-1111-1111-1111-111111111111"],
        grouping_mode=GroupingMode.SEPARATE.value,
        dest_fax="2402522141",
        sent_by="user@example.com",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    assert row.id is not None
    assert row.status == FaxLogStatus.QUEUED.value
    assert row.sent_at is not None
    assert row.ringcentral_message_id is None
    assert row.retry_of is None


def test_grouping_mode_values():
    assert {m.value for m in GroupingMode} == {"separate", "combined", "by_type"}


def test_fax_log_status_values():
    assert {s.value for s in FaxLogStatus} == {"queued", "sent", "delivered", "failed"}


def test_practice_config_roundtrip(db):
    db.add(PracticeConfig(key="ema_default_fax", value="2402522141"))
    db.commit()
    assert get_setting(db, "ema_default_fax") == "2402522141"
    assert get_setting(db, "missing_key", default="fallback") == "fallback"
