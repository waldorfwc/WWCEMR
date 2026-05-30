"""Seed default surgery-config rows on init.

Idempotent: re-running is a no-op once rows exist. Wired into
app.database.init_db().
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.surgery_config import Facility, SurgeryProcedureTemplate


DEFAULT_FACILITIES = [
    {"code": "office",  "label": "WWC Office — White Plains",
     "address": "White Plains, MD", "sort_order": 1},
    {"code": "medstar", "label": "MedStar Southern Maryland Hospital",
     "address": "7503 Surratts Rd, Clinton, MD", "sort_order": 2},
    {"code": "crmc",    "label": "University of MD Charles Regional",
     "address": "5 Garrett Ave, La Plata, MD", "sort_order": 3},
]


DEFAULT_TEMPLATES = [
    {"code": "office_30",   "name": "Office procedure (30 min)",
     "procedure_kind": "office",       "default_duration_minutes": 30},
    {"code": "minor_60",    "name": "Minor procedure (60 min)",
     "procedure_kind": "minor",        "default_duration_minutes": 60},
    {"code": "major_120",   "name": "Major procedure (120 min)",
     "procedure_kind": "major",        "default_duration_minutes": 120},
    {"code": "robotic_180", "name": "Robotic surgery (180 min)",
     "procedure_kind": "robotic_180",  "default_duration_minutes": 180,
     "default_cpt_code": "58571"},
    {"code": "robotic_240", "name": "Robotic surgery (240 min)",
     "procedure_kind": "robotic_240",  "default_duration_minutes": 240,
     "default_cpt_code": "58572"},
]


def seed_default_templates(db: Session) -> int:
    inserted = 0
    for t in DEFAULT_TEMPLATES:
        if db.query(SurgeryProcedureTemplate).filter(
                SurgeryProcedureTemplate.code == t["code"]).first():
            continue
        db.add(SurgeryProcedureTemplate(**t, created_by="seed", updated_by="seed"))
        inserted += 1
    if inserted:
        db.commit()
    return inserted


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
