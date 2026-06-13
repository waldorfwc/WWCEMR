"""Billing-document content_hash dedup: the partial unique index must be
scoped to LIVE rows so a soft-deleted-then-re-uploaded file doesn't block it,
and the diagnostic endpoint surfaces genuine live duplicates. (audit follow-up)
"""
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.billing_document import BillingDocument

_NEW_WHERE = "content_hash IS NOT NULL AND deleted_at IS NULL"
_OLD_WHERE = "content_hash IS NOT NULL"


def _doc(db, content_hash, *, deleted=False, name="d.pdf"):
    d = BillingDocument(
        original_filename=name,
        storage_filename=f"{name}-{content_hash}-{deleted}",  # unique col
        content_hash=content_hash,
        uploaded_by="a@b.c",
        uploaded_at=datetime(2026, 1, 1),
        deleted_at=datetime(2026, 1, 2) if deleted else None,
    )
    db.add(d); db.flush()
    return d


def _create_index(db, where):
    db.execute(text(
        "CREATE UNIQUE INDEX ix_test_bd_hash ON billing_documents "
        f"(content_hash) WHERE {where}"))


def test_scoped_index_builds_despite_soft_deleted_duplicate(db):
    # live A(h1), soft-deleted B(h1), live C(h2): only A & C are live and
    # their hashes differ -> the scoped index must build.
    _doc(db, "h1", name="A")
    _doc(db, "h1", deleted=True, name="B")
    _doc(db, "h2", name="C")
    db.flush()
    _create_index(db, _NEW_WHERE)   # must NOT raise
    # sanity: the OLD (unscoped) clause WOULD have failed on A+B
    db.execute(text("DROP INDEX ix_test_bd_hash"))
    with pytest.raises(IntegrityError):
        _create_index(db, _OLD_WHERE)


def test_scoped_index_still_rejects_genuine_live_duplicate(db):
    # two LIVE rows with the same hash (e.g. force=true upload) -> the scoped
    # index must still refuse to build; those are the dupes the diagnostic finds.
    _doc(db, "h9", name="A")
    _doc(db, "h9", name="B")
    db.flush()
    with pytest.raises(IntegrityError):
        _create_index(db, _NEW_WHERE)


def test_diagnostic_reports_only_live_duplicates(client, db):
    _doc(db, "live-dup", name="A")
    _doc(db, "live-dup", name="B")          # genuine live dup
    _doc(db, "del-dup", name="C")
    _doc(db, "del-dup", deleted=True, name="D")  # one live, one deleted -> NOT a dup
    _doc(db, "unique", name="E")
    db.commit()
    r = client.get("/api/admin/cleanup/billing-doc-duplicate-hashes")
    assert r.status_code == 200
    body = r.json()
    assert body["live_duplicate_hash_groups"] == 1
    assert body["total_redundant_docs"] == 1
    assert body["groups"][0]["content_hash"] == "live-dup"
    assert body["groups"][0]["count"] == 2
