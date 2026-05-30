"""Seed default surgery-config rows on init.

Idempotent: re-running is a no-op once rows exist. Wired into
app.database.init_db().
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.surgery_config import Facility


DEFAULT_FACILITIES = [
    {"code": "office",  "label": "WWC Office — White Plains",
     "address": "White Plains, MD", "sort_order": 1},
    {"code": "medstar", "label": "MedStar Southern Maryland Hospital",
     "address": "7503 Surratts Rd, Clinton, MD", "sort_order": 2},
    {"code": "crmc",    "label": "University of MD Charles Regional",
     "address": "5 Garrett Ave, La Plata, MD", "sort_order": 3},
]


def seed_default_facilities(db: Session) -> int:
    inserted = 0
    for f in DEFAULT_FACILITIES:
        exists = db.query(Facility).filter(Facility.code == f["code"]).first()
        if exists:
            continue
        db.add(Facility(**f, created_by="seed", updated_by="seed"))
        inserted += 1
    if inserted:
        db.commit()
    return inserted
