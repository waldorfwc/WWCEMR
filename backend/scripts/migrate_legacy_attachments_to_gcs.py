"""Backfill legacy local-path attachments into GCS.

Pre-migration rows in these tables reference Mac Mini absolute filesystem
paths (start with "/"). This script:
  1. For each table + path column, selects all rows where the path is a
     legacy absolute path
  2. Checks whether the file exists at that path on the host running the
     script (intended to run on the Mac Mini with the external drive
     mounted)
  3. Uploads the bytes to GCS under the appropriate prefix
  4. Updates the DB row with the new GCS object key

Idempotent: re-running on already-migrated rows finds nothing to do
because they no longer start with "/".

Usage (run on the Mac Mini):

    DATABASE_URL='postgresql+psycopg2://postgres:...@<ip>:5432/wwc_app?sslmode=require' \\
        ./venv/bin/python scripts/migrate_legacy_attachments_to_gcs.py

Optional flags:
    --dry-run    list what would migrate without writing anything
    --table NAME run only the named table (one of: pellet_order_attachments,
                 pellet_receipt_attachments, pellet_count_attachments,
                 surgery_files, active_claim_documents, appeal_letters,
                 bai2_imports)
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

from google.cloud import storage  # type: ignore
from sqlalchemy import create_engine, text


BUCKET = os.environ.get("DOCUMENTS_GCS_BUCKET", "wwc-app-docs")

# Each entry: (table, path_column, gcs_prefix). The path column always
# holds a `Text`/`varchar` value; the new key replaces it.
PLAN = [
    ("pellet_order_attachments",     "storage_path", "pellet-attachments"),
    ("pellet_receipt_attachments",   "storage_path", "pellet-attachments"),
    ("pellet_count_attachments",     "storage_path", "pellet-attachments"),
    ("surgery_files",                "path",         "surgery-files"),
    ("active_claim_documents",       "file_path",    "active-ar-docs"),
    ("appeal_letters",               "pdf_path",     "appeal-letters"),
    ("bai2_imports",                 "bai2_path",    "bank-recon"),
]


def migrate_table(conn, bucket, table: str, col: str, prefix: str,
                     dry_run: bool) -> tuple[int, int, int]:
    """Migrate rows for one table. Returns (migrated, missing, skipped)."""
    rows = conn.execute(text(
        f"SELECT id, {col} FROM {table} WHERE {col} LIKE '/%'"
    )).fetchall()
    if not rows:
        print(f"  no legacy rows")
        return (0, 0, 0)

    migrated = missing = skipped = 0
    for row_id, local_path in rows:
        p = Path(local_path)
        if not p.exists():
            print(f"  ✗ missing on disk: {local_path}")
            missing += 1
            continue
        if dry_run:
            print(f"  ⊙ would migrate: {local_path}")
            skipped += 1
            continue
        ext = p.suffix.lower()[:10]
        key = f"{prefix}/{uuid.uuid4().hex}{ext}"
        ct = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        try:
            bucket.blob(key).upload_from_filename(str(p), content_type=ct)
        except Exception as exc:
            print(f"  ✗ upload failed for {local_path}: {exc}")
            missing += 1
            continue
        conn.execute(text(
            f"UPDATE {table} SET {col} = :key WHERE id = :id"
        ), {"key": key, "id": row_id})
        migrated += 1
        if migrated % 25 == 0:
            print(f"  ... {migrated} migrated so far")
    return (migrated, missing, skipped)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                          help="list what would migrate without writing")
    parser.add_argument("--table", default=None,
                          help="run only the named table")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)
    eng = create_engine(db_url)
    client = storage.Client()
    bucket = client.bucket(BUCKET)

    plan = PLAN
    if args.table:
        plan = [e for e in PLAN if e[0] == args.table]
        if not plan:
            print(f"unknown table: {args.table}", file=sys.stderr)
            sys.exit(2)

    totals = {"migrated": 0, "missing": 0, "skipped": 0}
    with eng.begin() as conn:
        for table, col, prefix in plan:
            print(f"\n=== {table} ({col} → gs://{BUCKET}/{prefix}/) ===")
            m, miss, skip = migrate_table(conn, bucket, table, col, prefix,
                                                args.dry_run)
            totals["migrated"] += m
            totals["missing"] += miss
            totals["skipped"] += skip
            print(f"  done: {m} migrated, {miss} missing, {skip} skipped")

    print("\n" + "=" * 60)
    print(f"Total: {totals['migrated']} migrated, "
            f"{totals['missing']} missing on disk, "
            f"{totals['skipped']} skipped (dry-run)")
    if args.dry_run:
        print("\n(dry-run — no DB changes, no GCS writes)")


if __name__ == "__main__":
    main()
