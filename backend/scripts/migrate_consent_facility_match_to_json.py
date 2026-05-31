"""Migrate ConsentTemplate.facility_match from String(20) → JSON list.

Idempotent: detects which type the column currently is and only migrates
if needed.

Usage (one-shot, prod):
  DATABASE_URL='postgresql+psycopg2://...' \
      ./venv/bin/python scripts/migrate_consent_facility_match_to_json.py
"""
import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_URL not set", file=sys.stderr)
    sys.exit(2)

engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    # 1. Find current column type.
    col = conn.execute(text("""
        SELECT data_type FROM information_schema.columns
        WHERE table_name='consent_templates' AND column_name='facility_match'
    """)).first()
    if not col:
        print("ConsentTemplate.facility_match not found")
        sys.exit(0)
    print(f"Current data_type: {col[0]}")

    if col[0] in ("json", "jsonb"):
        print("Already JSON. Nothing to do.")
        sys.exit(0)

    # 2. Add a new JSON column, backfill, drop old, rename.
    print("Migrating: VARCHAR → JSON list")
    conn.execute(text("ALTER TABLE consent_templates ADD COLUMN facility_match_new JSON"))
    # Backfill existing rows: NULL/'' → [], single value → [value]
    conn.execute(text("""
        UPDATE consent_templates
           SET facility_match_new = CASE
               WHEN facility_match IS NULL OR facility_match = '' THEN '[]'::json
               ELSE ('["' || facility_match || '"]')::json
           END
    """))
    conn.execute(text("ALTER TABLE consent_templates DROP COLUMN facility_match"))
    conn.execute(text("""ALTER TABLE consent_templates
                          RENAME COLUMN facility_match_new TO facility_match"""))
    conn.execute(text("ALTER TABLE consent_templates ALTER COLUMN facility_match SET NOT NULL"))
    conn.execute(text("ALTER TABLE consent_templates ALTER COLUMN facility_match SET DEFAULT '[]'::json"))

    # 3. Sanity check
    count = conn.execute(text("SELECT COUNT(*) FROM consent_templates")).scalar()
    sample = conn.execute(text("SELECT name, facility_match FROM consent_templates LIMIT 3")).fetchall()
    print(f"Migrated {count} consent_template rows. Sample:")
    for row in sample:
        print(f"  {row[0]} -> {row[1]}")
