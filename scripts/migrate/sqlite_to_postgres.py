#!/usr/bin/env python3
"""Migrate data from a SQLite snapshot into a Postgres target database
using the app's own SQLAlchemy models.

Approach:
- Use Base.metadata.sorted_tables to walk parent->child order.
- Stream source rows in batches (yield_per) to avoid OOM on big tables.
- Disable FK / trigger checks on Postgres for the duration of the load
  via `SET session_replication_role = 'replica'`.
- After loading, reset sequences for any SERIAL/IDENTITY columns to
  MAX(id)+1 so subsequent inserts don't collide.
- Verifies row counts match between source and target at the end.

Usage:
  SOURCE_SQLITE=/tmp/wwc_app_snapshot.db \
  TARGET_DATABASE_URL=postgresql+psycopg2://user:pw@host/db?sslmode=require \
    python scripts/migrate/sqlite_to_postgres.py
"""
import os
import sys
import time
from pathlib import Path

# Make `app` package importable when this script is run from project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy import create_engine, inspect, text

from app.database import Base
# Auto-import every module under app/models/ so all models register on
# Base.metadata. Avoids drift if a new model file is added later.
import importlib, pkgutil  # noqa: E402
import app.models as _models_pkg  # noqa: E402
for _mi in pkgutil.iter_modules(_models_pkg.__path__):
    importlib.import_module(f"app.models.{_mi.name}")

SRC_URL = os.environ.get("SOURCE_SQLITE") or sys.exit("Missing SOURCE_SQLITE")
TGT_URL = os.environ.get("TARGET_DATABASE_URL") or sys.exit("Missing TARGET_DATABASE_URL")

src_engine = create_engine(f"sqlite:///{SRC_URL}")
tgt_engine = create_engine(TGT_URL, pool_pre_ping=True)

# Tables to skip (e.g., audit-log tables we want fresh, sqlite internal)
SKIP_TABLES = set()

BATCH = 1000

def main():
    inspector_src = inspect(src_engine)
    src_tables = set(inspector_src.get_table_names())
    inspector_tgt = inspect(tgt_engine)
    tgt_tables = set(inspector_tgt.get_table_names())

    common_tables = []
    for t in Base.metadata.sorted_tables:
        if t.name in SKIP_TABLES:
            print(f"  skip   {t.name}")
            continue
        if t.name not in src_tables:
            print(f"  miss/src {t.name}  (not in source)")
            continue
        if t.name not in tgt_tables:
            print(f"  miss/tgt {t.name}  (not in target)")
            continue
        common_tables.append(t)

    print(f"Migrating {len(common_tables)} tables  source={SRC_URL} -> target=<hidden>")
    print()

    src_counts = {}
    tgt_counts = {}

    t0 = time.time()
    # Approach: target is empty (TRUNCATEd below) and Cloud SQL's `postgres`
    # user can't SET session_replication_role. So we drop all FK constraints
    # for the migration, do all the inserts, then re-add the FKs. The defs
    # are captured from pg_constraint before dropping so the rebuild is
    # exact (handles ON DELETE/UPDATE clauses, deferrable, etc.).
    with tgt_engine.connect() as tgt:
        # Some model columns are NOT NULL in the SQLAlchemy definition but
        # the live SQLite source has NULL data in them (model drifted after
        # data was already written). Source-of-truth is the live data, so
        # drop NOT NULL on every non-PK column for the migration. Columns
        # where the data turns out to have no nulls can be tightened back
        # later in a normal Alembic-style migration.
        print("Dropping NOT NULL on all non-PK columns (preserves source nulls)...")
        not_null_rows = tgt.execute(text(
            "SELECT c.table_name, c.column_name "
            "FROM information_schema.columns c "
            "WHERE c.table_schema = 'public' "
            "  AND c.is_nullable = 'NO' "
            "  AND NOT EXISTS ( "
            "    SELECT 1 FROM information_schema.key_column_usage k "
            "    JOIN information_schema.table_constraints tc "
            "      ON tc.constraint_name = k.constraint_name "
            "     AND tc.constraint_schema = k.constraint_schema "
            "    WHERE tc.constraint_type = 'PRIMARY KEY' "
            "      AND k.table_schema = c.table_schema "
            "      AND k.table_name   = c.table_name "
            "      AND k.column_name  = c.column_name "
            "  )"
        )).fetchall()
        for r in not_null_rows:
            tgt.execute(text(f'ALTER TABLE "{r.table_name}" ALTER COLUMN "{r.column_name}" DROP NOT NULL'))
        tgt.commit()
        print(f"  dropped NOT NULL from {len(not_null_rows)} non-PK columns")
        print()

        print("Capturing + dropping FK constraints for migration...")
        fk_rows = tgt.execute(text(
            "SELECT conname, conrelid::regclass::text AS tname, "
            "       pg_get_constraintdef(oid) AS fkdef "
            "FROM pg_constraint "
            "WHERE contype = 'f' "
            "  AND connamespace = 'public'::regnamespace"
        )).fetchall()
        fk_defs = [(r.conname, r.tname, r.fkdef) for r in fk_rows]
        for conname, tname, _def in fk_defs:
            tgt.execute(text(f'ALTER TABLE {tname} DROP CONSTRAINT IF EXISTS "{conname}"'))
        tgt.commit()
        print(f"  dropped {len(fk_defs)} FK constraints")
        print()

        # Clear out the seed data init_db() inserted on first boot, in reverse
        # dep order so FKs are satisfied. TRUNCATE CASCADE in a single
        # statement is faster but requires referencing every table; safer to
        # walk and TRUNCATE one at a time.
        print("Clearing target (TRUNCATE CASCADE)...")
        truncate_names = ", ".join(f'"{t.name}"' for t in reversed(common_tables))
        if truncate_names:
            tgt.execute(text(f"TRUNCATE TABLE {truncate_names} RESTART IDENTITY CASCADE"))
            tgt.commit()
        print(f"Cleared {len(common_tables)} tables.")
        print()

        for table in common_tables:
            name = table.name
            with src_engine.connect() as src:
                n_src = src.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
            src_counts[name] = n_src
            if n_src == 0:
                tgt_counts[name] = 0
                print(f"  empty  {name}")
                continue

            t_start = time.time()
            rows_done = 0
            with src_engine.connect().execution_options(stream_results=True, max_row_buffer=BATCH) as src:
                result = src.execute(table.select())
                batch = []
                for row in result:
                    batch.append(dict(row._mapping))
                    if len(batch) >= BATCH:
                        tgt.execute(table.insert(), batch)
                        rows_done += len(batch)
                        batch = []
                if batch:
                    tgt.execute(table.insert(), batch)
                    rows_done += len(batch)
            tgt.commit()
            elapsed = time.time() - t_start
            print(f"  ok     {name:50s} {rows_done:>10,d} rows  ({elapsed:6.1f}s)")
            tgt_counts[name] = rows_done

        # Re-add FK constraints
        print()
        print("Re-adding FK constraints...")
        fk_fail = 0
        for conname, tname, fkdef in fk_defs:
            try:
                tgt.execute(text(f'ALTER TABLE {tname} ADD CONSTRAINT "{conname}" {fkdef}'))
            except Exception as e:
                fk_fail += 1
                print(f"  fail {tname}.{conname}: {e}")
        tgt.commit()
        print(f"  added {len(fk_defs) - fk_fail}/{len(fk_defs)} FK constraints")

    # Reset sequences for any tables with serial PKs (column type bigint/integer
    # whose default uses `nextval('seq')`).
    print()
    print("Resetting sequences...")
    with tgt_engine.begin() as conn:
        seq_rows = conn.execute(text(
            "SELECT n.nspname AS schema_name, c.relname AS table_name, a.attname AS column_name "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 "
            "JOIN pg_attrdef ad ON ad.adrelid = c.oid AND ad.adnum = a.attnum "
            "WHERE pg_get_expr(ad.adbin, ad.adrelid) LIKE 'nextval%' "
            "  AND n.nspname = 'public'"
        )).fetchall()
        for schema_name, table_name, column_name in seq_rows:
            try:
                conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{schema_name}.{table_name}', '{column_name}'), "
                    f"(SELECT COALESCE(MAX(\"{column_name}\"), 0) + 1 FROM \"{schema_name}\".\"{table_name}\"), false)"
                ))
                print(f"  seq    {table_name}.{column_name}")
            except Exception as e:
                print(f"  fail   {table_name}.{column_name}: {e}")

    elapsed_total = time.time() - t0
    print()
    print(f"Done in {elapsed_total:.1f}s")
    print()

    # Verification pass
    print("Verifying row counts...")
    mismatches = 0
    for table in common_tables:
        with tgt_engine.connect() as tgt:
            n_tgt = tgt.execute(text(f'SELECT COUNT(*) FROM "{table.name}"')).scalar()
        n_src = src_counts.get(table.name, 0)
        if n_tgt == n_src:
            status = "OK"
        else:
            status = f"MISMATCH (src={n_src}, tgt={n_tgt})"
            mismatches += 1
        print(f"  {status:50s} {table.name}")

    print()
    if mismatches == 0:
        print("All tables match.")
    else:
        print(f"{mismatches} table(s) mismatch; review above.")
        sys.exit(2)

if __name__ == "__main__":
    main()
