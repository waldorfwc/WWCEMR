#!/usr/bin/env python3
"""Backfill SHA-256 content_hash on existing BillingDocument rows.

Idempotent — only touches rows where content_hash IS NULL. Streams the
file in 1 MB chunks so large PDFs don't blow up RAM. Missing files
(deleted on disk but still in DB) are logged + skipped, leaving the
content_hash NULL so a future re-run can pick them up if the file
reappears.

Usage:
  cd backend
  DATABASE_URL=... ./venv/bin/python -m scripts.backfill_billing_doc_hashes

Or as a one-shot Cloud Run Job — but easiest is to run locally against
whichever DB the live deployment uses, via the Cloud SQL Auth Proxy.
"""
import hashlib
import sys
from pathlib import Path

# Make `app` importable when run from project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.billing_document import BillingDocument
from app.services import billing_doc_storage as storage


CHUNK = 1024 * 1024  # 1 MB


def sha256_of(storage_filename: str) -> str | None:
    try:
        with storage.open_for_read(storage_filename) as f:
            h = hashlib.sha256()
            while True:
                buf = f.read(CHUNK)
                if not buf:
                    break
                h.update(buf)
            return h.hexdigest()
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"  ERR  {storage_filename}: {e}")
        return None


def main():
    db: Session = SessionLocal()
    try:
        rows = (db.query(BillingDocument)
                  .filter(BillingDocument.content_hash.is_(None))
                  .order_by(BillingDocument.uploaded_at.asc())
                  .all())
        total = len(rows)
        print(f"Found {total} BillingDocument rows with NULL content_hash.")
        if not total:
            return

        ok = 0
        missing = 0
        dup_groups: dict[str, list[str]] = {}

        for i, d in enumerate(rows, 1):
            h = sha256_of(d.storage_filename)
            if h is None:
                missing += 1
                print(f"  {i:>5}/{total}  MISS  {d.id}  {d.original_filename}")
                continue
            d.content_hash = h
            dup_groups.setdefault(h, []).append(str(d.id))
            ok += 1
            if i % 50 == 0:
                db.commit()
                print(f"  {i:>5}/{total}  committed ({ok} ok, {missing} missing so far)")
        db.commit()

        # Report duplicates that already exist in the DB.
        dups = {h: ids for h, ids in dup_groups.items() if len(ids) > 1}
        print()
        print(f"Hashed: {ok}.  Missing files: {missing}.  Total: {total}.")
        if dups:
            print(f"Existing duplicate clusters: {len(dups)}")
            for h, ids in list(dups.items())[:20]:
                print(f"  {h[:12]}…  {len(ids)} rows: {ids}")
            if len(dups) > 20:
                print(f"  … and {len(dups) - 20} more clusters")
        else:
            print("No duplicates among hashed rows.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
