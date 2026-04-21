"""Tests for the in-memory import session store."""
from datetime import datetime, timedelta, timezone
import pytest
from app.services import import_sessions as store


@pytest.fixture(autouse=True)
def _clear_store():
    store._sessions.clear()
    yield
    store._sessions.clear()


def _entry(id_: str, now=None):
    return store.SessionEntry(
        session_id=id_,
        payload={"hello": "world"},
        filename="test.xls",
        file_path="/tmp/test.xls",
        user_email="u@example.com",
        created_at=now or datetime.now(timezone.utc),
        expires_at=(now or datetime.now(timezone.utc)) + timedelta(minutes=30),
    )


def test_put_and_get_roundtrip():
    e = _entry("abc")
    store.put(e)
    got = store.get("abc")
    assert got is not None
    assert got.filename == "test.xls"
    assert got.payload == {"hello": "world"}


def test_get_returns_none_for_unknown_id():
    assert store.get("nope") is None


def test_get_returns_none_for_expired_entry_and_purges():
    past = datetime.now(timezone.utc) - timedelta(minutes=60)
    e = store.SessionEntry(
        session_id="old",
        payload={},
        filename="t",
        file_path="/tmp/t",
        user_email="u@x",
        created_at=past,
        expires_at=past + timedelta(minutes=30),  # still in the past
    )
    store.put(e)
    assert store.get("old") is None
    assert "old" not in store._sessions


def test_purge_removes_entry():
    store.put(_entry("zap"))
    store.purge("zap")
    assert "zap" not in store._sessions


def test_purge_missing_is_noop():
    store.purge("missing")  # should not raise
