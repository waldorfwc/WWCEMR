"""bank-recon BAI2 output — GCS via storage adapter."""
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch


def _seed_bai2_import(db, **kw):
    from app.models.bai2 import Bai2Import
    imp = Bai2Import(
        csv_filename="x.csv", csv_path="/tmp/x.csv",
        bank_name="Bank", account_full="acct",
        bai2_filename=kw.get("bai2_filename", "BAI2.txt"),
        bai2_path=kw.get("bai2_path", "bank-recon/key.txt"),
        date_range_start=date(2026, 1, 1),
        date_range_end=date(2026, 1, 31),
        csv_row_count=1, transactions_included=1,
        skipped_withdrawal=0, skipped_modmed=0, skipped_stripe=0,
        skipped_zero=0, skipped_duplicate_in_file=0,
        skipped_prior_imports=0,
        total_amount=Decimal("0"),
        generated_by="tester@example.com",
    )
    db.add(imp); db.commit(); db.refresh(imp)
    return imp


def test_download_bai2_via_serve_blob(client, db):
    imp = _seed_bai2_import(db)
    from fastapi.responses import Response
    with patch("app.routers.bank_recon.serve_blob",
                return_value=Response(content=b"01,02,...",
                                          media_type="text/plain")) as mock:
        r = client.get(f"/api/bank-recon/imports/{imp.id}/download")
    assert r.status_code == 200, r.text
    _, kwargs = mock.call_args
    assert kwargs["gcs_object"] == "bank-recon/key.txt"
    assert kwargs["local_path"] is None


def test_download_bai2_legacy_path_returns_410(client, db):
    imp = _seed_bai2_import(db,
                                  bai2_path="/var/data/old/bai2.txt")
    r = client.get(f"/api/bank-recon/imports/{imp.id}/download")
    assert r.status_code == 410


def test_download_bai2_404_when_no_path(client, db):
    imp = _seed_bai2_import(db, bai2_path=None)
    r = client.get(f"/api/bank-recon/imports/{imp.id}/download")
    assert r.status_code == 404
