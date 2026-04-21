"""One-time migration — delete Payment rows whose claim_id no longer resolves.

Caused by Phase 2b's claim wipe which didn't touch the payments table.

Usage (from backend/):
    source venv/bin/activate
    python -m app.scripts.prune_orphan_payments --yes-i-am-sure
"""
import argparse
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import SessionLocal


def run(confirm: bool, session: Optional[Session] = None) -> int:
    if not confirm:
        raise SystemExit("Refusing to run without --yes-i-am-sure flag.")
    db = session if session is not None else SessionLocal()
    owns_db = session is None
    try:
        result = db.execute(text(
            "DELETE FROM payments WHERE claim_id IS NOT NULL AND "
            "claim_id NOT IN (SELECT id FROM claims)"
        ))
        deleted = result.rowcount
        db.commit()
    finally:
        if owns_db:
            db.close()
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes-i-am-sure", action="store_true")
    args = parser.parse_args()
    deleted = run(confirm=args.yes_i_am_sure)
    print(f"Pruned {deleted} orphan Payment rows.")


if __name__ == "__main__":
    main()
