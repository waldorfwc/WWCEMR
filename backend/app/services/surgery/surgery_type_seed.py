"""One-time, idempotent seed of the SurgeryType catalog from the legacy
hardcoded PROCEDURES list. After this runs, the catalog is the source of truth
and is fully editable; PROCEDURES is retained only as the seed source.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.surgery_type import SurgeryType
from app.services.surgery.picklists import PROCEDURES
from app.services.surgery.smartsheet_seed import MAJOR_CPTS


def seed_surgery_types(db: Session) -> int:
    """Insert one SurgeryType per PROCEDURES entry, but only when the table is
    empty. Returns the number of rows inserted (0 if already seeded)."""
    if db.query(SurgeryType).count() > 0:
        return 0
    inserted = 0
    for i, proc in enumerate(PROCEDURES):
        cpt = (proc.get("cpt") or "").strip()
        desc = (proc.get("description") or "").strip()
        db.add(SurgeryType(
            name=desc,
            cpts=[{"cpt": cpt, "description": desc}],
            classification="major" if cpt in MAJOR_CPTS else "minor",
            eligible_facilities=[],
            consent_template_ids=[],
            active=True,
            sort_order=i,
        ))
        inserted += 1
    db.commit()
    return inserted
