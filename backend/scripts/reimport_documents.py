"""
Rebuild patient_documents from DocumentIndex.csv (source of truth).

Reads the CSV, verifies each file exists under EXTRACT_ROOT, and inserts
rows into patient_documents. chart_number is taken directly from the CSV's
PatientID column.

Run from backend/ with: python scripts/reimport_documents.py
"""
import csv
import os
import sys
import uuid
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.database import engine, SessionLocal
from app.models.document import PatientDocument  # noqa

CSV_PATH = "/Volumes/OWC External/Data Export/DocumentIndex.csv"
EXTRACT_ROOT = "/Volumes/OWC External/ExtractedDocuments/Document"
BATCH_SIZE = 5000
MISSING_LOG = "/Users/wwcclaudecode/Documents/wwc-era-project/reimport_missing.txt"


def parse_date(s: str):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def build_extracted_set():
    """Walk EXTRACT_ROOT once, return set of relative paths like '10001/Foo.pdf'."""
    root = Path(EXTRACT_ROOT)
    print(f"Walking {root}...", flush=True)
    paths = set()
    for p in root.rglob("*"):
        if p.is_file():
            paths.add(str(p.relative_to(root)))
    print(f"  {len(paths):,} files on disk", flush=True)
    return paths


def csv_path_to_relative(file_path: str) -> str:
    """'\\10001\\Foo.pdf' -> '10001/Foo.pdf'"""
    return file_path.lstrip("\\").replace("\\", "/")


def main():
    if not os.path.exists(CSV_PATH):
        print(f"ERROR: {CSV_PATH} not found")
        sys.exit(1)
    if not os.path.exists(EXTRACT_ROOT):
        print(f"ERROR: {EXTRACT_ROOT} not found")
        sys.exit(1)

    extracted = build_extracted_set()

    print("Purging patient_documents table...", flush=True)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM patient_documents"))
    print("  done", flush=True)

    print(f"Streaming {CSV_PATH}...", flush=True)
    missing = []
    inserted = 0
    errors = 0
    batch = []
    now = datetime.utcnow()

    with open(CSV_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        session = SessionLocal()
        try:
            for i, row in enumerate(reader, start=1):
                try:
                    rel = csv_path_to_relative(row["FilePath"])
                    if rel not in extracted:
                        missing.append(rel)
                        continue

                    size_bytes = int(row["FileSize"] or 0)
                    page = int(row["Series"] or 1)

                    batch.append({
                        "id": str(uuid.uuid4()),
                        "chart_number": row["PatientID"].strip(),
                        "doc_type": row["DocumentTypeDescription"].strip() or "Unknown",
                        "doc_date": parse_date(row["DateLastModified"]),
                        "doc_id": row["DocumentID"].strip() or None,
                        "page_number": page,
                        "filename": row["FileName"].strip(),
                        "file_path": f"{EXTRACT_ROOT}/{rel}",
                        "file_size_kb": size_bytes // 1024,
                        "indexed_at": now,
                    })

                    if len(batch) >= BATCH_SIZE:
                        session.execute(
                            text(
                                "INSERT INTO patient_documents "
                                "(id, chart_number, doc_type, doc_date, doc_id, page_number, "
                                " filename, file_path, file_size_kb, indexed_at) "
                                "VALUES (:id, :chart_number, :doc_type, :doc_date, :doc_id, "
                                " :page_number, :filename, :file_path, :file_size_kb, :indexed_at)"
                            ),
                            batch,
                        )
                        session.commit()
                        inserted += len(batch)
                        batch = []
                        if inserted % 50000 == 0:
                            print(f"  inserted {inserted:,}  missing {len(missing):,}", flush=True)
                except Exception as e:
                    errors += 1
                    if errors <= 10:
                        print(f"  row {i} error: {e}", flush=True)

            if batch:
                session.execute(
                    text(
                        "INSERT INTO patient_documents "
                        "(id, chart_number, doc_type, doc_date, doc_id, page_number, "
                        " filename, file_path, file_size_kb, indexed_at) "
                        "VALUES (:id, :chart_number, :doc_type, :doc_date, :doc_id, "
                        " :page_number, :filename, :file_path, :file_size_kb, :indexed_at)"
                    ),
                    batch,
                )
                session.commit()
                inserted += len(batch)
        finally:
            session.close()

    with open(MISSING_LOG, "w") as f:
        for m in missing:
            f.write(m + "\n")

    print("")
    print("=" * 60)
    print(f"Inserted:        {inserted:,}")
    print(f"Missing on disk: {len(missing):,}  (logged to {MISSING_LOG})")
    print(f"Row errors:      {errors:,}")
    print("=" * 60)


if __name__ == "__main__":
    main()
