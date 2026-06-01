"""Idempotent migration for Patient Portal P2.

Adds: schedule_gate_override(+_at, +_by) columns on surgeries.

Run on prod:
    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/migrate_patient_portal_p2.py
"""
import os
import sys
from sqlalchemy import create_engine, text

DDL = [
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS schedule_gate_override BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS schedule_gate_override_at TIMESTAMP NULL""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS schedule_gate_override_by VARCHAR(120) NULL""",
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
