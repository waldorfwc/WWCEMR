"""Materialize pellet patients who are due for re-insertion into the shared
recall engine (RecallEntry, recall_type='Pellet Re-insertion'), reusing the
canonical recall_is_due computation. Idempotent; never resets call progress."""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session, joinedload

from app.models.pellet import PelletPatient
from app.models.recall import RecallEntry
from app.utils.dt import now_utc_naive

PELLET_RECALL_TYPE = "Pellet Re-insertion"


def _to_date(s):
    return date.fromisoformat(s) if s else None


def materialize_pellet_recalls(db: Session) -> dict:
    """Upsert a RecallEntry for each active, recall-due pellet patient; complete
    entries whose patient is no longer due. Suppressed entries are left alone."""
    from app.routers.pellet import _patient_view_extras
    today = now_utc_naive().date()
    existing = {e.chart_number: e for e in
                db.query(RecallEntry)
                  .filter(RecallEntry.recall_type == PELLET_RECALL_TYPE).all()}
    seen: set = set()
    created = updated = completed = 0

    patients = (db.query(PelletPatient)
                  .filter(PelletPatient.status == "active")
                  .options(joinedload(PelletPatient.visits)).all())
    for p in patients:
        x = _patient_view_extras(p, today)
        if not x.get("recall_is_due"):
            continue
        seen.add(p.chart_number)
        e = existing.get(p.chart_number)
        if e is not None and e.status == "suppressed":
            continue
        if e is None:
            e = RecallEntry(chart_number=p.chart_number, recall_type=PELLET_RECALL_TYPE,
                            source="pellet", status="active")
            db.add(e); created += 1
        else:
            if e.status != "active":
                e.status = "active"
            updated += 1
        e.patient_name = p.patient_name
        e.dob = p.patient_dob
        e.cell_phone = p.patient_phone
        e.email = p.patient_email
        e.primary_insurance = p.primary_insurance
        e.recall_due = _to_date(x.get("recall_due_date"))
        e.last_visit = _to_date(x.get("last_visit_date"))

    for chart, e in existing.items():
        if chart not in seen and e.status == "active":
            e.status = "completed"; completed += 1

    db.commit()
    return {"created": created, "updated": updated, "completed": completed}
