"""B2 — auto-exclusion is identity-based (date + amount + last_4).

The old behavior auto-excluded any candidate whose DATE fell inside a
prior import's date range, silently dropping genuinely-new transactions
that merely shared a posting date with a prior import. The fix: a
candidate is auto-excluded only when its (date, amount, last_4) matches
a transaction ALREADY stored in a prior BAI2 file. Everything else
imports regardless of date; manual exclusion still wins.

These tests drive the real /preview and /generate endpoints. The test
storage backend is "local", so preview writes the CSV (and snapshot) to
a temp dir and generate reads it back — no storage mocking needed.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.bai2 import Bai2Import, Bai2Transaction
from app.routers.bank_recon import _prior_identities, _identity, _q2


@pytest.fixture(autouse=True)
def _local_storage_root(tmp_path, monkeypatch):
    """Point the local storage backend at a writable temp dir so /preview
    can cache the CSV + snapshot and /generate can read them back. (The
    default /var/data/wwc-docs isn't writable in the test environment.)"""
    from app.config import settings
    monkeypatch.setattr(settings, "documents_local_root", str(tmp_path))


@pytest.fixture(autouse=True)
def _stub_pg_advisory_lock(db):
    """/generate serializes concurrent calls with a Postgres advisory lock
    (pg_advisory_xact_lock). SQLite has no such function, so register a
    no-op on the test connection — the serialization isn't what we're
    testing here, the identity-dedup decision is."""
    raw = db.connection().connection  # the underlying sqlite3 connection
    raw.create_function("pg_advisory_xact_lock", 1, lambda _k: None)


# ──────────────────────────────────────────────────────────────────────
# Unit tests for the identity helpers

def test_q2_normalizes_amount_to_two_dp():
    assert _q2(123.4) == Decimal("123.40")
    assert _q2("123.40") == Decimal("123.40")
    assert _q2(Decimal("123.400001")) == Decimal("123.40")
    assert _q2(123.4) == _q2(Decimal("123.4"))
    assert _q2(None) == Decimal("0.00")


def test_prior_identities_and_identity_match(db):
    _seed_prior(db)
    prior = _prior_identities(db)
    assert (date(2026, 5, 1), Decimal("500.00"), "1234") in prior

    class _T:
        transaction_date = date(2026, 5, 1)
        amount = 500.0
        last_4 = "1234"

    assert _identity(_T()) in prior

    class _Other:
        transaction_date = date(2026, 5, 3)
        amount = 250.0
        last_4 = "9999"

    assert _identity(_Other()) not in prior


# ──────────────────────────────────────────────────────────────────────
# Endpoint-driven integration tests

def _seed_prior(db):
    """A prior import covering 05/01..05/05 with one stored transaction:
    05/01, $500, last4 1234."""
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
        import_id=imp.id,
        transaction_date=date(2026, 5, 1),
        description="ACH DEP SOMEPAYER HCCLAIMPMT xxxx1234",
        formatted_text="SomePayer ACH x1234",
        amount=Decimal("500.00"), last_4="1234",
        method="ACH", bai_type_code="195",
        dedup_key="prior-key-1",
    ))
    db.commit()
    return imp


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


def test_reworded_duplicate_is_already_imported_and_not_generated(client, db):
    """Same (date, amount, last4) as a prior txn but DIFFERENT description →
    already_imported True, and /generate does NOT insert it."""
    _seed_prior(db)
    # Re-worded duplicate of the prior 05/01 $500 x1234 deposit + a brand new
    # 05/03 $250 x9999 deposit whose date is inside the prior range.
    csv_bytes = (
        b"Date,Description,Amount\n"
        b"05/01/2026,DEPOSIT SOMEPAYER REMITTANCE ADVICE xxxx1234,500.00\n"
        b"05/03/2026,ACH DEP NEWPAYER HCCLAIMPMT xxxx9999,250.00\n"
    )
    preview = _preview(client, csv_bytes)

    dup = _txn(preview, "1234")
    newcomer = _txn(preview, "9999")

    assert dup["already_imported"] is True
    # The bug fix: a new deposit whose date is inside the prior range but
    # has no identity match is NOT auto-excluded.
    assert newcomer["already_imported"] is False
    assert preview["stats"]["already_imported_count"] == 1
    assert "date_covered_count" not in preview["stats"]

    r = client.post("/api/bank-recon/generate", json={
        "preview_id": preview["preview_id"],
        "csv_filename": "bank.csv",
        "ext": preview["ext"],
        "bank_name": "PNC x395",
        "excluded_keys": [],
    })
    assert r.status_code == 200, r.text
    body = r.json()

    # Only the newcomer is imported; the re-worded dup is skipped as prior.
    assert body["transactions_included"] == 1
    assert body["skipped_prior_imports"] == 1
    assert float(body["total_amount"]) == 250.00

    # The newcomer's transaction now lives in the DB.
    stored = (
        db.query(Bai2Transaction)
        .filter(Bai2Transaction.last_4 == "9999")
        .all()
    )
    assert len(stored) == 1
    assert stored[0].amount == Decimal("250.00")
    assert stored[0].transaction_date == date(2026, 5, 3)


def test_new_txn_inside_prior_date_range_is_imported(client, db):
    """A candidate whose DATE is inside the prior import's range but with no
    identity match imports regardless of date (different amount/last4)."""
    _seed_prior(db)
    csv_bytes = (
        b"Date,Description,Amount\n"
        # same date + last4 as prior but DIFFERENT amount → not a match
        b"05/01/2026,ACH DEP SOMEPAYER HCCLAIMPMT xxxx1234,600.00\n"
        # same date + amount as prior but DIFFERENT last4 → not a match
        b"05/01/2026,ACH DEP OTHER HCCLAIMPMT xxxx5678,500.00\n"
    )
    preview = _preview(client, csv_bytes)
    for t in preview["transactions"]:
        assert t["already_imported"] is False
    assert preview["stats"]["already_imported_count"] == 0

    r = client.post("/api/bank-recon/generate", json={
        "preview_id": preview["preview_id"],
        "csv_filename": "bank.csv",
        "ext": preview["ext"],
        "bank_name": "PNC x395",
        "excluded_keys": [],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transactions_included"] == 2
    assert body["skipped_prior_imports"] == 0


def test_manual_exclusion_wins_over_identity(client, db):
    """A candidate with no prior identity match but whose dedup_key the user
    put in excluded_keys is NOT imported — manual exclusion wins."""
    _seed_prior(db)
    csv_bytes = (
        b"Date,Description,Amount\n"
        b"05/03/2026,ACH DEP NEWPAYER HCCLAIMPMT xxxx9999,250.00\n"
    )
    preview = _preview(client, csv_bytes)
    newcomer = _txn(preview, "9999")
    assert newcomer["already_imported"] is False

    r = client.post("/api/bank-recon/generate", json={
        "preview_id": preview["preview_id"],
        "csv_filename": "bank.csv",
        "ext": preview["ext"],
        "bank_name": "PNC x395",
        "excluded_keys": [newcomer["dedup_key"]],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transactions_included"] == 0
    assert body["skipped_user_excluded"] == 1
    # Not counted as a prior duplicate (it wasn't one) — user excluded it.
    assert body["skipped_prior_imports"] == 0
    assert (
        db.query(Bai2Transaction)
        .filter(Bai2Transaction.last_4 == "9999")
        .count()
    ) == 0
