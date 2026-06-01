"""Idempotent migration for Patient Portal P5.

Adds: new table `surgery_documents`.

Run on prod:
    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/migrate_patient_portal_p5.py
"""
import os
import sys
from sqlalchemy import create_engine, text

DDL = [
    """CREATE TABLE IF NOT EXISTS surgery_documents (
        id            CHAR(36) PRIMARY KEY,
        surgery_id    CHAR(36) NOT NULL REFERENCES surgeries(id) ON DELETE CASCADE,
        kind          VARCHAR(40) NOT NULL,
        filename      VARCHAR(255) NOT NULL,
        gcs_path      VARCHAR(500) NOT NULL,
        content_type  VARCHAR(100) NULL,
        size_bytes    INTEGER NULL,
        uploaded_at   TIMESTAMP NOT NULL DEFAULT NOW(),
        uploaded_by   VARCHAR(120) NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS ix_surgery_documents_surgery_id
       ON surgery_documents(surgery_id)""",
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr); sys.exit(2)
    eng = create_engine(db_url)
    with eng.begin() as conn:
        for ddl in DDL:
            conn.execute(text(ddl))
            print(f"  ✓ {ddl.split(chr(10))[0][:80]}")
    print("\nDone.")


if __name__ == "__main__":
    main()
