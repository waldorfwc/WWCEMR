"""BAI2 preview→generate uses GCS-backed CSV cache (not local disk)."""
from unittest.mock import patch


def test_parse_csv_from_bytes_round_trips_a_simple_csv():
    from app.services.bai2_generator import parse_csv_from_bytes, FilterOptions
    csv_bytes = (b"Date,Description,Amount\n"
                  b"2026-05-01,SOMEPAYER HCCLAIMPMT,123.45\n"
                  b"2026-05-02,STRIPE TRANSFER,9.99\n")
    out = parse_csv_from_bytes(csv_bytes,
                                  FilterOptions(skip_stripe=True))
    assert out.csv_row_count == 2
    assert out.skipped_stripe == 1
    assert len(out.transactions) == 1
    assert out.transactions[0].amount == 123.45


def test_parse_csv_from_bytes_handles_utf8_bom():
    """Banks sometimes export with a UTF-8 BOM; the bytes parser should
    strip it via utf-8-sig like the legacy path-based loader did."""
    from app.services.bai2_generator import parse_csv_from_bytes, FilterOptions
    csv_bytes = b"\xef\xbb\xbfDate,Description,Amount\n2026-05-01,X,1.00\n"
    out = parse_csv_from_bytes(csv_bytes, FilterOptions())
    assert out.csv_row_count == 1
    # The first column header should be "Date", not "﻿Date"
    assert len(out.transactions) == 1


def test_parse_csv_path_wrapper_still_works(tmp_path):
    from app.services.bai2_generator import parse_csv, FilterOptions
    p = tmp_path / "bank.csv"
    p.write_bytes(b"Date,Description,Amount\n2026-05-01,X,1.00\n")
    out = parse_csv(str(p), FilterOptions())
    assert out.csv_row_count == 1


def test_preview_caches_csv_via_save_blob_with_key(client, db):
    """Preview should write the CSV to gs://wwc-app-docs/bank-recon-csv/
    with a deterministic key keyed by preview_id+ext."""
    # preview_csv writes TWO blobs via save_blob_with_key: the raw CSV
    # (keyed {pid}{ext}) and a filter snapshot (keyed {pid}.snapshot.json).
    # Capture every call and assert on the CSV-specific one.
    calls = []
    def _capture_save(*, key, body, content_type=None):
        calls.append({"key": key, "body": body, "content_type": content_type})
        return key

    with patch("app.routers.bank_recon.save_blob_with_key",
                side_effect=_capture_save):
        r = client.post(
            "/api/bank-recon/preview",
            files={"file": ("bank.csv",
                              b"Date,Description,Amount\n2026-05-01,X,1.00\n",
                              "text/csv")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    pid = body["preview_id"]
    ext = body["ext"]
    assert ext == ".csv"
    csv_call = next(c for c in calls if c["key"] == f"bank-recon-csv/{pid}{ext}")
    assert csv_call["content_type"] == "text/csv"
    assert b"2026-05-01" in csv_call["body"]
    # The snapshot blob is also written for /generate to consume.
    assert any(c["key"] == f"bank-recon-csv/{pid}.snapshot.json" for c in calls)


def test_generate_reads_csv_via_read_blob(client, db):
    """Generate should look up the preview CSV by key — not by filesystem
    path. Returns 404 with friendly message if the key isn't found."""
    # /generate serializes concurrent calls with a Postgres advisory lock
    # (pg_advisory_xact_lock). SQLite has no such function, so register a
    # no-op on the test connection.
    db.connection().connection.create_function(
        "pg_advisory_xact_lock", 1, lambda _k: None)
    # preview_id must be 32 hex chars (Field pattern guards the csv key from
    # path-escape); use a conforming id so we exercise the read_blob lookup
    # rather than tripping request validation (422).
    with patch("app.routers.bank_recon.read_blob",
                side_effect=FileNotFoundError("not found")):
        r = client.post("/api/bank-recon/generate", json={
            "preview_id": "a" * 32,
            "csv_filename": "bank.csv",
            "ext": ".csv",
        })
    assert r.status_code == 404
    assert "re-upload" in r.json()["detail"].lower()
