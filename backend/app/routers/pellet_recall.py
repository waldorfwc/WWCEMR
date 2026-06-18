"""Pellet Recall — a worklist of pellet patients due for re-insertion, surfaced
through the shared recall engine and gated by the pellet module. List + detail
are pellet-specific (insertion history); claim/dial/outcome delegate to the
recall handlers (Task 3)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.pellet import PelletPatient
from app.models.recall import RecallCallLog, RecallEntry
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.routers.recalls import _entry_to_dict, _taxonomy
from app.services.pellet.recall_sync import (materialize_pellet_recalls,
                                             PELLET_RECALL_TYPE)
from app.services.pellet.settings import cfg

router = APIRouter(prefix="/pellets/recall", tags=["pellet-recall"])


def _load_pellet_entry(db: Session, recall_id: str) -> RecallEntry:
    e = db.query(RecallEntry).filter(RecallEntry.id == recall_id).first()
    if e is None or e.recall_type != PELLET_RECALL_TYPE:
        raise HTTPException(status_code=404, detail="pellet recall not found")
    return e


@router.post("/sync")
def sync(db: Session = Depends(get_db),
         current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.WORK))):
    return materialize_pellet_recalls(db)


@router.get("")
def list_pellet_recalls(
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW)),
):
    q = (db.query(RecallEntry)
           .filter(RecallEntry.recall_type == PELLET_RECALL_TYPE,
                   RecallEntry.status == "active"))
    if search:
        like = f"%{search.strip()}%"
        q = q.filter((RecallEntry.patient_name.ilike(like)) |
                     (RecallEntry.chart_number.ilike(like)))
    rows = q.order_by(desc(RecallEntry.recall_due)).all()
    return {"items": [_entry_to_dict(e) for e in rows]}


def _effective_date(v):
    """The visit's display/sort date: inserted date, else scheduled date."""
    return (v.inserted_at.date() if v.inserted_at else v.scheduled_date) or date.min


def _insertion_history(db: Session, chart_number: str) -> list[dict]:
    p = (db.query(PelletPatient)
           .filter(PelletPatient.chart_number == chart_number)
           .options(joinedload(PelletPatient.visits)).first())
    if p is None:
        return []
    out = []
    for v in sorted(p.visits or [], key=_effective_date, reverse=True):  # newest first
        eff = _effective_date(v)
        doses = "; ".join(f"{d.dose_type.label} ×{d.quantity}"
                          for d in (v.doses or []) if d.dose_type)
        out.append({"date": eff.strftime("%m/%d/%Y") if eff != date.min else None,
                    "location": v.location, "provider": v.provider,
                    "status": v.status, "visit_kind": v.visit_kind, "doses": doses or None})
    return out


@router.get("/{recall_id}")
def get_pellet_recall(recall_id: str, db: Session = Depends(get_db),
                      current_user: dict = Depends(requires_tier(Module.PELLETS, Tier.VIEW))):
    e = _load_pellet_entry(db, recall_id)
    db.add(RecallCallLog(recall_entry_id=e.id, chart_number=e.chart_number,
                         event_type="detail_viewed",
                         user_email=current_user.get("email")))
    db.commit()
    logs = (db.query(RecallCallLog)
              .filter(RecallCallLog.recall_entry_id == e.id)
              .order_by(desc(RecallCallLog.occurred_at)).limit(50).all())
    permanent, cooldown, completed, all_labels = _taxonomy(db)
    return {
        "recall": _entry_to_dict(e, dob=e.dob),
        "insertion_history": _insertion_history(db, e.chart_number),
        "caller_script": cfg(db, "recall_caller_script"),
        "outcomes": list(all_labels),
        "history": [
            {"id": str(l.id), "event_type": l.event_type, "user_email": l.user_email,
             "occurred_at": str(l.occurred_at), "outcome": l.outcome,
             "notes": l.notes, "duration_seconds": l.duration_seconds}
            for l in logs
        ],
    }
