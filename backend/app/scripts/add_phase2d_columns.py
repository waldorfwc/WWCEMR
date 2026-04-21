"""One-time migration — add Phase 2d workflow columns to the claims table.

Adds: follow_up_date, follow_up_reason, last_submission_date, claim_state.
Idempotent: re-runs check existing columns first via PRAGMA table_info.

Usage (from backend/):
    source venv/bin/activate
    python -m app.scripts.add_phase2d_columns
"""
from typing import Dict, List, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import SessionLocal

NEW_COLUMNS = [
    ("follow_up_date", "DATE"),
    ("follow_up_reason", "VARCHAR(200)"),
    ("last_submission_date", "DATE"),
    ("claim_state", "VARCHAR(20)"),
]


def run(session: Optional[Session] = None) -> Dict[str, List[str]]:
    db = session if session is not None else SessionLocal()
    owns_db = session is None
    added: List[str] = []
    skipped: List[str] = []
    try:
        existing = {row[1] for row in db.execute(text("PRAGMA table_info(claims)")).fetchall()}
        for name, type_ in NEW_COLUMNS:
            if name in existing:
                skipped.append(name)
                continue
            db.execute(text(f"ALTER TABLE claims ADD COLUMN {name} {type_}"))
            added.append(name)
        db.commit()
    finally:
        if owns_db:
            db.close()
    return {"added": added, "skipped": skipped}


def main() -> None:
    result = run()
    for col in result["added"]:
        print(f"  + {col}")
    for col in result["skipped"]:
        print(f"  = {col} (already exists)")


if __name__ == "__main__":
    main()
