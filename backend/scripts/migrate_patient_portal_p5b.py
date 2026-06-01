"""Idempotent migration for Patient Portal P5b.

Adds:
  - 3 columns on `surgeries`: fmla_fee_paid(+_at, +_stripe_session)
  - 1 column on `surgery_payments`: kind (default 'patient_balance')

Run on prod:
    DATABASE_URL='postgresql+psycopg2://...' \
        ./venv/bin/python scripts/migrate_patient_portal_p5b.py
"""
import os
import sys
from sqlalchemy import create_engine, text

DDL = [
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS fmla_fee_paid BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS fmla_fee_paid_at TIMESTAMP NULL""",
    """ALTER TABLE surgeries
       ADD COLUMN IF NOT EXISTS fmla_fee_stripe_session VARCHAR(100) NULL""",
    """ALTER TABLE surgery_payments
       ADD COLUMN IF NOT EXISTS kind VARCHAR(40) NOT NULL DEFAULT 'patient_balance'""",
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
