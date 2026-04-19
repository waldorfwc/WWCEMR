#!/usr/bin/env python3
"""
Watch ~/Documents for new intake folders (named like '1970', '1974 2', etc.)
Index + match, then move to ~/Documents/_archive.
"""

import os
import re
import shutil
import time
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from app.database import SessionLocal, init_db
from app.models.patient_directory import IntakeDocument
from app.services.patient_resolver import match_intake_to_charts
from sqlalchemy import func, extract, distinct

WATCH_DIR = os.path.expanduser("~/Documents")
EXTERNAL_ARCHIVE = "/Volumes/OWC External/IntakeArchive"
LOCAL_ARCHIVE = os.path.join(WATCH_DIR, "_archive")
ARCHIVE_DIR = EXTERNAL_ARCHIVE if os.path.isdir("/Volumes/OWC External") else LOCAL_ARCHIVE
FOLDER_RE = re.compile(r"^\d{4}(\s+\d+)?$")
PATIENT_RE = re.compile(r"^(.+?)\s+(\d{2})-(\d{2})-(\d{4})$")
YEAR_CAT_RE = re.compile(r"^(\d{4})\s*[-–]")

PROCESSED = set()

init_db()


def update_paths_after_archive(old_base: str, new_base: str):
    """Update file_path in intake_documents after folder is moved to _archive."""
    db = SessionLocal()
    try:
        docs = db.query(IntakeDocument).filter(
            IntakeDocument.file_path.like(f"{old_base}%")
        ).all()
        for d in docs:
            d.file_path = d.file_path.replace(old_base, new_base, 1)
        db.commit()
        if docs:
            print(f"  Updated {len(docs)} file paths to archive location")
    finally:
        db.close()


ZIP_RE = re.compile(r"^\d{4}.*\.zip$", re.IGNORECASE)


def find_intake_folders():
    """Find folders or zip files in WATCH_DIR that look like birth-year intake items."""
    found = []
    for name in os.listdir(WATCH_DIR):
        path = os.path.join(WATCH_DIR, name)
        if path in PROCESSED or name == "_archive":
            continue

        # Match year folders (e.g., "1970", "1970 2")
        if os.path.isdir(path) and FOLDER_RE.match(name):
            found.append((name, path, "folder"))

        # Match year zip files (e.g., "1970.zip", "1970 2.zip", "1970-20260413T....zip")
        if os.path.isfile(path) and ZIP_RE.match(name):
            found.append((name, path, "zip"))

    return found


def index_folder(folder_path):
    """Index all intake documents in a folder and return count."""
    db = SessionLocal()
    batch = []
    count = 0

    try:
        for root, dirs, files in os.walk(folder_path):
            for fname in files:
                if fname.startswith("."):
                    continue
                full_path = os.path.join(root, fname)
                rel = os.path.relpath(full_path, folder_path)
                parts = rel.split(os.sep)

                for i, p in enumerate(parts):
                    m = PATIENT_RE.match(p.strip())
                    if m:
                        name = m.group(1).strip()
                        try:
                            dob = date(int(m.group(4)), int(m.group(2)), int(m.group(3)))
                        except ValueError:
                            break
                        category = parts[i + 1] if i + 1 < len(parts) - 1 else None
                        doc_year = None
                        if category:
                            ym = YEAR_CAT_RE.match(category)
                            if ym:
                                doc_year = int(ym.group(1))
                        try:
                            size_kb = os.path.getsize(full_path) // 1024
                        except OSError:
                            size_kb = 0
                        ext = os.path.splitext(fname)[1].lower().lstrip(".")

                        batch.append(IntakeDocument(
                            patient_name_raw=name, dob=dob, doc_category=category,
                            doc_year=doc_year, filename=fname, file_path=full_path,
                            file_size_kb=size_kb, file_type=ext, match_confidence="pending",
                        ))
                        count += 1
                        break

                if len(batch) >= 500:
                    db.bulk_save_objects(batch)
                    db.commit()
                    batch = []

        if batch:
            db.bulk_save_objects(batch)
            db.commit()

        # Run matching
        result = match_intake_to_charts(db)
        total_all = db.query(func.count(IntakeDocument.id)).scalar()

        return count, result, total_all
    finally:
        db.close()


def archive_folder(name, path):
    """Move folder to _archive."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    dest = os.path.join(ARCHIVE_DIR, name)
    # Handle name collision
    if os.path.exists(dest):
        n = 1
        while os.path.exists(f"{dest}_{n}"):
            n += 1
        dest = f"{dest}_{n}"
    shutil.move(path, dest)
    return dest


def main():
    print(f"Watching {WATCH_DIR} for intake folders...")
    print(f"Archive destination: {ARCHIVE_DIR}")
    print(f"Press Ctrl+C to stop.\n")

    while True:
        items = find_intake_folders()
        for name, path, item_type in items:

            # Wait for download to finish (file size stabilizes)
            prev_size = -1
            for _ in range(5):
                if item_type == "zip":
                    cur_size = os.path.getsize(path)
                else:
                    cur_size = sum(os.path.getsize(os.path.join(r, f))
                                   for r, _, fs in os.walk(path) for f in fs)
                if cur_size == prev_size and cur_size > 0:
                    break
                prev_size = cur_size
                time.sleep(2)

            # If zip, extract first
            work_path = path
            extracted_tmp = None
            if item_type == "zip":
                import zipfile
                extracted_tmp = os.path.join(WATCH_DIR, f"_unzipping_{name}")
                print(f"[{time.strftime('%H:%M:%S')}] Unzipping: {name} ({os.path.getsize(path) // (1024*1024)} MB)")
                try:
                    with zipfile.ZipFile(path, 'r') as zf:
                        zf.extractall(extracted_tmp)
                except Exception as e:
                    print(f"  ERROR unzipping {name}: {e}")
                    PROCESSED.add(path)
                    if extracted_tmp and os.path.isdir(extracted_tmp):
                        shutil.rmtree(extracted_tmp)
                    continue
                work_path = extracted_tmp

            file_count = sum(1 for _, _, f in os.walk(work_path) for _ in f if not _.startswith("."))
            if file_count == 0:
                PROCESSED.add(path)
                if extracted_tmp and os.path.isdir(extracted_tmp):
                    shutil.rmtree(extracted_tmp)
                continue

            print(f"[{time.strftime('%H:%M:%S')}] Processing: {name} ({file_count} files)")

            # Index and match
            count, result, total_all = index_folder(work_path)
            exact_pct = round(result["exact"] / total_all * 100, 1) if total_all else 0

            print(f"  Indexed: {count} files")
            print(f"  Match: exact={result['exact']} fuzzy={result.get('fuzzy_low',0)+result.get('fuzzy_high',0)} unmatched={result['unmatched']}")
            print(f"  Running total: {total_all} docs, {exact_pct}% exact match")

            # Archive
            if item_type == "zip":
                # Move zip to archive, update paths from tmp to archive/extracted
                os.makedirs(ARCHIVE_DIR, exist_ok=True)
                # Move extracted folder to archive
                archive_name = os.path.splitext(name)[0]  # strip .zip
                dest = os.path.join(ARCHIVE_DIR, archive_name)
                if os.path.exists(dest):
                    n = 1
                    while os.path.exists(f"{dest}_{n}"):
                        n += 1
                    dest = f"{dest}_{n}"
                shutil.move(work_path, dest)
                update_paths_after_archive(work_path, dest)
                # Move the zip file too
                shutil.move(path, os.path.join(ARCHIVE_DIR, name))
            else:
                dest = archive_folder(name, work_path)
                update_paths_after_archive(work_path, dest)

            PROCESSED.add(path)
            print(f"  Archived to: {dest}")
            print()

        time.sleep(5)


if __name__ == "__main__":
    main()
