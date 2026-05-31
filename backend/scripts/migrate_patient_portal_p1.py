"""Idempotent migration for Patient Portal P1.

Adds:
  - 4 columns on `surgeries`: labs_self_reported(+_at),
    hospital_preop_self_reported(+_at)
  - new table `patient_portal_auth_codes`

Run on prod:
    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/migrate_patient_portal_p1.py
"""
import os
import sys

from sqlalchemy import create_engine, text

DDL = [
    # surgeries — 4 new columns, default false / null
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS labs_self_reported BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS labs_self_reported_at TIMESTAMP NULL""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS hospital_preop_self_reported BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS hospital_preop_self_reported_at TIMESTAMP NULL""",
    # patient_portal_auth_codes — new table
    """CREATE TABLE IF NOT EXISTS patient_portal_auth_codes (
        id               UUID PRIMARY KEY,
        surgery_id       UUID NOT NULL REFERENCES surgeries(id) ON DELETE CASCADE,
        challenge_token  VARCHAR(64) NOT NULL UNIQUE,
        code_hash        VARCHAR(60) NOT NULL,
        fail_count       INTEGER NOT NULL DEFAULT 0,
        expires_at       TIMESTAMP NOT NULL,
        used_at          TIMESTAMP NULL,
        created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
        sent_to_phone    VARCHAR(40) NULL
    )""",
    """CREATE INDEX IF NOT EXISTS ix_patient_portal_auth_codes_surgery
       ON patient_portal_auth_codes(surgery_id)""",
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
