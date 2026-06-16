"""Authenticated walk-through of the bank-recon dedup fix, driving the real
/preview and /generate endpoints: pending dropped silently, auto-exclusion
only on a true date+amount+last4 match against a prior BAI file, new same-date
transactions imported regardless of date, manual exclusion still wins."""
from datetime import date
from decimal import Decimal

import pytest

from app.models.bai2 import Bai2Import, Bai2Transaction


@pytest.fixture(autouse=True)
def _local_storage_root(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "documents_local_root", str(tmp_path))


@pytest.fixture(autouse=True)
def _stub_pg_advisory_lock(db):
    db.connection().connection.create_function("pg_advisory_xact_lock", 1, lambda _k: None)


def _seed_prior(db):
    imp = Bai2Import(
        csv_filename="prior.csv", csv_path="bank-recon-csv/prior.csv",
        bank_name="PNC x395", bai2_filename="PRIOR.bai", bai2_path="bank-recon/prior.bai",
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
        formatted_text="SomePayer ACH x1234", amount=Decimal("500.00"),
        last_4="1234", method="ACH", bai_type_code="195", dedup_key="prior-key-1"))
    db.commit()


def _find(preview, last4):
    return next((t for t in preview["transactions"] if (t["last_4"] or "") == last4), None)


def test_bank_recon_walkthrough(client, db, capsys):
    log = []
    _seed_prior(db)
    log.append("prior BAI file: 05/01 $500 last4 1234 (range 05/01–05/05)")

    csv_bytes = (
        b"Date,Description,Amount\n"
        b"PENDING - 05/04/2026,ACH DEP PENDINGPAYER xxxx5555,300.00\n"     # pending → ignored
        b"05/01/2026,DEPOSIT SOMEPAYER REMITTANCE ADVICE xxxx1234,500.00\n"  # re-worded dup
        b"05/03/2026,ACH DEP NEWPAYER HCCLAIMPMT xxxx9999,250.00\n"          # NEW, in prior range
        b"05/02/2026,ACH DEP OTHERPAYER xxxx7777,125.00\n"                   # NEW, will manually exclude
    )
    preview = _preview = client.post("/api/bank-recon/preview",
        files={"file": ("bank.csv", csv_bytes, "text/csv")}).json()

    # 1. Pending never appears.
    assert _find(preview, "5555") is None
    log.append("1. uploaded 4 rows incl 1 PENDING → preview shows 3 (pending dropped, uncounted)")

    # 2. Identity match flags only the true duplicate.
    assert _find(preview, "1234")["already_imported"] is True
    assert _find(preview, "9999")["already_imported"] is False
    assert _find(preview, "7777")["already_imported"] is False
    assert preview["stats"]["already_imported_count"] == 1
    assert "date_covered_count" not in preview["stats"]
    log.append("2. only the re-worded 05/01 $500 x1234 → already_imported (date+amount+last4 match)")
    log.append("   05/03 x9999 + 05/02 x7777 → NOT excluded though their dates are inside the prior range")

    # 3. Generate, manually excluding the 05/02 x7777 deposit.
    excl = _find(preview, "7777")["dedup_key"]
    body = client.post("/api/bank-recon/generate", json={
        "preview_id": preview["preview_id"], "csv_filename": "bank.csv",
        "ext": preview["ext"], "bank_name": "PNC x395", "excluded_keys": [excl],
    }).json()
    log.append(f"3. generate (manually excluded x7777): imported={body['transactions_included']}, "
               f"skipped_prior={body['skipped_prior_imports']}, total=${float(body['total_amount']):.2f}")

    # Only the genuinely-new, non-excluded 05/03 deposit imported.
    assert body["transactions_included"] == 1
    assert body["skipped_prior_imports"] == 1                 # the re-worded dup
    assert float(body["total_amount"]) == 250.00
    stored = db.query(Bai2Transaction).filter(Bai2Transaction.last_4 == "9999").all()
    assert len(stored) == 1 and stored[0].transaction_date == date(2026, 5, 3)
    assert db.query(Bai2Transaction).filter(Bai2Transaction.last_4 == "7777").count() == 0
    log.append("4. result: 05/03 x9999 imported (bug fix); dup auto-excluded; x7777 manually excluded; pending lost-to-nobody")

    with capsys.disabled():
        print("\n  ── bank-recon dedup fix walk-through (authenticated) ──")
        print("   " + log[0])
        for line in log[1:]:
            print("   " + line)
