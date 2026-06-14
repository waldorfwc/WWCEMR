"""billing-doc-dedup: dry-run by default, soft-deletes redundant duplicates,
keeps the canonical (most-progressed/earliest) row. (audit follow-up)"""
from datetime import datetime

from app.models.billing_document import BillingDocument


def _doc(db, content_hash, *, status="new", name="d.pdf", uploaded=datetime(2026, 1, 1)):
    d = BillingDocument(
        original_filename=name,
        storage_filename=f"{name}-{content_hash}-{status}-{uploaded.isoformat()}",
        content_hash=content_hash,
        status=status,
        uploaded_by="a@b.c",
        uploaded_at=uploaded,
    )
    db.add(d); db.flush()
    return d


def test_dry_run_reports_plan_but_deletes_nothing(client, db):
    a = _doc(db, "h1", status="new", name="A", uploaded=datetime(2026, 1, 1))
    b = _doc(db, "h1", status="worked", name="B", uploaded=datetime(2026, 1, 2))
    db.commit()
    r = client.post("/api/admin/cleanup/billing-doc-dedup")  # dry_run defaults True
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["groups"] == 1
    assert body["would_soft_delete"] == 1
    # keeper is the 'worked' row (more progressed), not the earlier 'new' one
    grp = body["plan"][0]
    assert grp["keep"]["id"] == str(b.id)
    assert grp["soft_delete"][0]["id"] == str(a.id)
    # nothing actually deleted
    db.expire_all()
    assert db.query(BillingDocument).filter(BillingDocument.deleted_at.isnot(None)).count() == 0


def test_commit_soft_deletes_redundant_keeps_one(client, db):
    _doc(db, "hX", status="new", name="A", uploaded=datetime(2026, 1, 1))
    _doc(db, "hX", status="new", name="B", uploaded=datetime(2026, 1, 2))
    _doc(db, "hX", status="new", name="C", uploaded=datetime(2026, 1, 3))
    _doc(db, "hY", status="new", name="solo")  # not a dup
    db.commit()
    r = client.post("/api/admin/cleanup/billing-doc-dedup?dry_run=false")
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is False
    assert body["soft_deleted"] == 2  # 3 in group hX -> keep 1, delete 2
    db.expire_all()
    live = db.query(BillingDocument).filter(
        BillingDocument.content_hash == "hX",
        BillingDocument.deleted_at.is_(None)).all()
    assert len(live) == 1  # exactly one canonical row remains
    deleted = db.query(BillingDocument).filter(
        BillingDocument.content_hash == "hX",
        BillingDocument.deleted_at.isnot(None)).all()
    assert len(deleted) == 2
    assert all((d.deleted_by or "").startswith("dedup:") for d in deleted)


def test_no_duplicates_is_noop(client, db):
    _doc(db, "uniq1", name="A")
    _doc(db, "uniq2", name="B")
    db.commit()
    r = client.post("/api/admin/cleanup/billing-doc-dedup?dry_run=false")
    assert r.status_code == 200
    assert r.json()["soft_deleted"] == 0
