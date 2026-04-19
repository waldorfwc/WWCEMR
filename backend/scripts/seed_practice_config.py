"""Idempotent seed for practice_config: ema_default_fax + ema_fax_label.

Run once after deploy, or whenever adding new settings.
Safe to run repeatedly — existing rows are left alone.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.practice_config import PracticeConfig


DEFAULTS = {
    "ema_default_fax": "2402522141",
    "ema_fax_label": "ModMed EMA",
}


def main():
    init_db()
    db = SessionLocal()
    try:
        for key, value in DEFAULTS.items():
            existing = db.query(PracticeConfig).filter(PracticeConfig.key == key).first()
            if existing:
                print(f"  [skip] {key} already set to {existing.value!r}")
                continue
            db.add(PracticeConfig(key=key, value=value))
            print(f"  [add]  {key} = {value!r}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
