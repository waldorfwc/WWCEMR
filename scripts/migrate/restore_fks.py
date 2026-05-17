#!/usr/bin/env python3
"""Rebuild FK constraints on the target Postgres database from the
SQLAlchemy model definitions. Used after a data migration that dropped
all FK constraints (so the inserts could happen in any order).

Usage:
  TARGET_DATABASE_URL=postgresql+psycopg2://user:pw@host/db?sslmode=require \
    python scripts/migrate/restore_fks.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import importlib, pkgutil
from sqlalchemy import create_engine
from sqlalchemy.schema import AddConstraint

from app.database import Base
import app.models as _models_pkg
for _mi in pkgutil.iter_modules(_models_pkg.__path__):
    importlib.import_module(f"app.models.{_mi.name}")

TGT_URL = os.environ.get("TARGET_DATABASE_URL") or sys.exit("Missing TARGET_DATABASE_URL")
tgt_engine = create_engine(TGT_URL)

added = 0
failed = 0
total = 0
# Each FK in its own transaction so one failure doesn't poison the rest.
for table in Base.metadata.sorted_tables:
    for fkc in table.foreign_key_constraints:
        total += 1
        try:
            with tgt_engine.begin() as conn:
                conn.execute(AddConstraint(fkc))
            added += 1
        except Exception as e:
            failed += 1
            msg = str(e).splitlines()[0][:160]
            print(f"  FAIL {table.name}.{fkc.name or '(unnamed)'}  {msg}")

print()
print(f"Restored {added}/{total} FK constraints  ({failed} failed)")
if failed:
    sys.exit(2)
