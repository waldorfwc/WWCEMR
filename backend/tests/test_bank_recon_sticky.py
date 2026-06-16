"""B2 — sticky per-transaction exclusions are persisted + enforced.

Drives the real /preview and /generate endpoints (local storage backend,
no mocking). When a user excludes an otherwise-importable transaction at
/generate, a Bai2Exclusion is persisted by identity (date+amount+last_4)
and future uploads of the same transaction are auto-blocked, even if the
client sends excluded_keys=[]. Excluding an already-imported dupe does NOT
create a sticky exclusion (that's just "don't double-import").
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.bai2 import Bai2Import, Bai2Transaction
from app.models.bai2_exclusion import Bai2Exclusion


@pytest.fixture(autouse=True)
def _local_storage_root(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "documents_local_root", str(tmp_path))


@pytest.fixture(autouse=True)
def _stub_pg_advisory_lock(db):
    raw = db.connection().connection
    raw.create_function("pg_advisory_xact_lock", 1, lambda _k: None)


def _preview(client, csv_bytes):
    r = client.post(
        "/api/bank-recon/preview",
        files={"file": ("bank.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _txn(preview, last4):
    for t in preview["transactions"]:
        if (t["last_4"] or "") == last4:
            return t
    raise AssertionError(f"no candidate with last4 {last4!r} in preview")


def _seed_prior(db):
    """Prior import storing one txn: 05/01, $500, last4 1234."""
    imp = Bai2Import(
        csv_filename="prior.csv", csv_path="bank-recon-csv/prior.csv",
        bank_name="PNC x395", account_last_4=None,
        bai2_filename="PRIOR.bai", bai2_path="bank-recon/prior.bai",
        date_range_start=date(2026, 5, 1), date_range_end=date(2026, 5, 5),
        csv_row_count=1, transactions_included=1,
        skipped_withdrawal=0, skipped_modmed=0, skipped_stripe=0,
        skipped_zero=0, skipped_duplicate_in_file=0, skipped_prior_imports=0,
        total_amount=Decimal("500.00"), generated_by="tester@example.com",
    )
    db.add(imp); db.flush()
    db.add(Bai2Transaction(
        import_id=imp.id, transaction_date=date(2026, 5, 1),
        description="ACH DEP SOMEPAYER HCCLAIMPMT xxxx1234",
        formatted_text="SomePayer ACH x1234",
        amount=Decimal("500.00"), last_4="1234",
        method="ACH", bai_type_code="195", dedup_key="prior-key-1",
    ))
    db.commit()
    return imp


def test_excluding_non_dup_creates_sticky_and_skips_import(client, db):
    csv_bytes = (
        b"Date,Description,Amount\n"
        b"05/03/2026,ACH DEP NEWPAYER HCCLAIMPMT xxxx9999,250.00\n"
    )
    preview = _preview(client, csv_bytes)
    newcomer = _txn(preview, "9999")
    assert newcomer["already_imported"] is False
    assert newcomer["previously_excluded"] is False

    r = client.post("/api/bank-recon/generate", json={
        "preview_id": preview["preview_id"],
        "csv_filename": "bank.csv",
        "ext": preview["ext"],
        "bank_name": "PNC x395",
        "excluded_keys": [newcomer["dedup_key"]],
        "exclusion_reason": "not a real deposit",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transactions_included"] == 0
    assert body["skipped_user_excluded"] == 1
    assert body["skipped_sticky"] == 0  # not yet sticky on the run that created it

    # A sticky exclusion row was persisted, active.
    excl = db.query(Bai2Exclusion).filter(
        Bai2Exclusion.deleted_at.is_(None)).all()
    assert len(excl) == 1
    e = excl[0]
    assert e.transaction_date == date(2026, 5, 3)
    assert e.amount == Decimal("250.00")
    assert e.last_4 == "9999"
    assert e.reason == "not a real deposit"
    assert e.excluded_by == "tester@waldorfwomenscare.com"

    # The transaction was NOT imported.
    assert db.query(Bai2Transaction).filter(
        Bai2Transaction.last_4 == "9999").count() == 0


def test_reupload_of_sticky_txn_is_previously_excluded_and_blocked(client, db):
    # First: exclude the 9999 txn so it becomes sticky.
    csv1 = (
        b"Date,Description,Amount\n"
        b"05/03/2026,ACH DEP NEWPAYER HCCLAIMPMT xxxx9999,250.00\n"
    )
    p1 = _preview(client, csv1)
    n1 = _txn(p1, "9999")
    r1 = client.post("/api/bank-recon/generate", json={
        "preview_id": p1["preview_id"], "csv_filename": "bank.csv",
        "ext": p1["ext"], "bank_name": "PNC x395",
        "excluded_keys": [n1["dedup_key"]],
    })
    assert r1.status_code == 200, r1.text

    # Re-upload a CSV with the SAME date+amount+last4 (different wording).
    csv2 = (
        b"Date,Description,Amount\n"
        b"05/03/2026,DEPOSIT NEWPAYER REMITTANCE xxxx9999,250.00\n"
    )
    p2 = _preview(client, csv2)
    again = _txn(p2, "9999")
    assert again["previously_excluded"] is True
    assert p2["stats"]["previously_excluded_count"] == 1

    # Generate with excluded_keys=[] must still NOT import it (sticky wins).
    r2 = client.post("/api/bank-recon/generate", json={
        "preview_id": p2["preview_id"], "csv_filename": "bank2.csv",
        "ext": p2["ext"], "bank_name": "PNC x395",
        "excluded_keys": [],
    })
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["transactions_included"] == 0
    assert body["skipped_sticky"] == 1
    assert db.query(Bai2Transaction).filter(
        Bai2Transaction.last_4 == "9999").count() == 0
    # Still exactly one active sticky row (reactivated, not duplicated).
    assert db.query(Bai2Exclusion).filter(
        Bai2Exclusion.deleted_at.is_(None)).count() == 1


def test_excluding_already_imported_dup_does_not_create_sticky(client, db):
    _seed_prior(db)
    # Re-worded dup of the prior 05/01 $500 x1234 deposit.
    csv_bytes = (
        b"Date,Description,Amount\n"
        b"05/01/2026,DEPOSIT SOMEPAYER REMITTANCE ADVICE xxxx1234,500.00\n"
    )
    preview = _preview(client, csv_bytes)
    dup = _txn(preview, "1234")
    assert dup["already_imported"] is True

    # User also unchecks it (excluded) — but it's a prior dupe, so NO sticky.
    r = client.post("/api/bank-recon/generate", json={
        "preview_id": preview["preview_id"], "csv_filename": "bank.csv",
        "ext": preview["ext"], "bank_name": "PNC x395",
        "excluded_keys": [dup["dedup_key"]],
    })
    assert r.status_code == 200, r.text
    assert db.query(Bai2Exclusion).count() == 0


# ──────────────────────────────────────────────────────────────────────
# B3 — admin list + reinstate

def _make_sticky(client, db, last4="9999", amount="250.00"):
    csv_bytes = (
        b"Date,Description,Amount\n"
        + f"05/03/2026,ACH DEP NEWPAYER HCCLAIMPMT xxxx{last4},{amount}\n".encode()
    )
    p = _preview(client, csv_bytes)
    n = _txn(p, last4)
    r = client.post("/api/bank-recon/generate", json={
        "preview_id": p["preview_id"], "csv_filename": "bank.csv",
        "ext": p["ext"], "bank_name": "PNC x395",
        "excluded_keys": [n["dedup_key"]],
    })
    assert r.status_code == 200, r.text
    return db.query(Bai2Exclusion).filter(
        Bai2Exclusion.last_4 == last4).first()


def test_list_exclusions_returns_active_row(client, db):
    e = _make_sticky(client, db)
    r = client.get("/api/bank-recon/exclusions")
    assert r.status_code == 200, r.text
    rows = r.json()["exclusions"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == str(e.id)
    assert row["transaction_date"] == "2026-05-03"
    assert row["amount"] == 250.00
    assert row["last_4"] == "9999"
    assert "reinstated_at" not in row  # active row has no reinstated fields


def test_reinstate_flips_row_and_unblocks_reimport(client, db):
    e = _make_sticky(client, db)
    eid = str(e.id)

    r = client.post(f"/api/bank-recon/exclusions/{eid}/reinstate")
    assert r.status_code == 200, r.text
    assert r.json()["reinstated"] is True
    db.expire_all()
    refreshed = db.query(Bai2Exclusion).filter(Bai2Exclusion.id == eid).first()
    assert refreshed.deleted_at is not None  # soft-deleted == reinstated

    # No longer in the default (active) list.
    rows = client.get("/api/bank-recon/exclusions").json()["exclusions"]
    assert rows == []
    # But visible with include_reinstated.
    rows2 = client.get(
        "/api/bank-recon/exclusions?include_reinstated=true").json()["exclusions"]
    assert len(rows2) == 1
    assert rows2[0]["reinstated_at"] is not None

    # Re-uploading the same txn now IMPORTS (no longer blocked).
    csv2 = (
        b"Date,Description,Amount\n"
        b"05/03/2026,ACH DEP NEWPAYER HCCLAIMPMT xxxx9999,250.00\n"
    )
    p2 = _preview(client, csv2)
    again = _txn(p2, "9999")
    assert again["previously_excluded"] is False
    r2 = client.post("/api/bank-recon/generate", json={
        "preview_id": p2["preview_id"], "csv_filename": "bank2.csv",
        "ext": p2["ext"], "bank_name": "PNC x395", "excluded_keys": [],
    })
    assert r2.status_code == 200, r2.text
    assert r2.json()["transactions_included"] == 1
    assert db.query(Bai2Transaction).filter(
        Bai2Transaction.last_4 == "9999").count() == 1


def test_reinstate_missing_is_404_and_idempotent(client, db):
    import uuid as _uuid
    r = client.post(f"/api/bank-recon/exclusions/{_uuid.uuid4()}/reinstate")
    assert r.status_code == 404

    e = _make_sticky(client, db)
    eid = str(e.id)
    r1 = client.post(f"/api/bank-recon/exclusions/{eid}/reinstate")
    assert r1.json()["reinstated"] is True
    r2 = client.post(f"/api/bank-recon/exclusions/{eid}/reinstate")
    assert r2.status_code == 200
    assert r2.json()["reinstated"] is False  # idempotent
