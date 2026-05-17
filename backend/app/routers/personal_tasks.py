"""Personal task list endpoints — employee-owned to-do items with
optional subtasks, assignees, and view-only sharing.

Access rules (uniformly applied per row):
  - `owner_email` — created the task; full control.
  - `assignee_email` — primary doer; may toggle status / close.
  - `shared_with` (JSON list) — view-only access.

Any of the above can see the task; only owner / assignee can mutate
(subject to per-endpoint restrictions documented below).
"""
from __future__ import annotations

from datetime import date as _date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.personal_task import PersonalTask, PRIORITIES, STATUSES
from app.routers.auth import get_current_user

router = APIRouter(prefix="/personal-tasks", tags=["personal-tasks"])


def _norm_email(s: Optional[str]) -> Optional[str]:
    return (s or "").lower().strip() or None


def _ensure_visible(task: PersonalTask, email: str) -> None:
    """Raises 404 (not 403, to avoid disclosing existence) when caller
    doesn't have at least read access to this task."""
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if email == task.owner_email:
        return
    if email in (task.assignees or []):
        return
    if email in (task.shared_with or []):
        return
    raise HTTPException(status_code=404, detail="task not found")


def _can_edit_content(task: PersonalTask, email: str) -> bool:
    """Owner, any assignee, OR anyone in shared_with may edit the task's
    content (title/description/priority/due_date/status). Only the owner
    can change ownership-related fields (assignees, shared_with) or
    delete the task."""
    if email == task.owner_email:
        return True
    if email in (task.assignees or []):
        return True
    return email in (task.shared_with or [])


def _ensure_can_delete(task: PersonalTask, email: str) -> None:
    """Owner only — sharing means collaborate, not nuke."""
    if email != task.owner_email:
        raise HTTPException(status_code=403,
                            detail="only the owner can delete this task")


def _serialize(task: PersonalTask, *, subtasks: Optional[list[PersonalTask]] = None) -> dict:
    children = subtasks or []
    total = len(children)
    closed = sum(1 for c in children if c.status == "closed")
    pct = int(round((closed / total) * 100)) if total else None
    out = {
        "id":             str(task.id),
        "parent_id":      str(task.parent_id) if task.parent_id else None,
        "owner_email":    task.owner_email,
        "assignees":      list(task.assignees or []),
        "shared_with":    list(task.shared_with or []),
        "title":          task.title,
        "description":    task.description,
        "priority":       task.priority,
        "status":         task.status,
        "due_date":       str(task.due_date) if task.due_date else None,
        "position":       task.position,
        "closed_at":      task.closed_at.isoformat() if task.closed_at else None,
        "closed_by":      task.closed_by,
        "created_at":     task.created_at.isoformat() if task.created_at else None,
        "created_by":     task.created_by,
        "updated_at":     task.updated_at.isoformat() if task.updated_at else None,
        "updated_by":     task.updated_by,
        "subtasks":       [_serialize(c) for c in
                            sorted(children, key=lambda x: (x.position, x.created_at))],
        "subtask_total":  total,
        "subtask_closed": closed,
        "percent":        pct,
    }
    return out


def _maybe_close_parent_if_all_subtasks_done(db: Session, parent_id, *, by: str) -> None:
    """When the last open subtask of a parent closes, auto-close the
    parent. Idempotent — does nothing if the parent is already closed."""
    if not parent_id:
        return
    parent = db.query(PersonalTask).filter(PersonalTask.id == parent_id).first()
    if not parent or parent.status == "closed":
        return
    siblings = (db.query(PersonalTask)
                  .filter(PersonalTask.parent_id == parent_id).all())
    if siblings and all(s.status == "closed" for s in siblings):
        parent.status = "closed"
        parent.closed_at = datetime.utcnow()
        parent.closed_by = by
        parent.updated_by = by
        db.add(parent)


# ─── Payloads ───────────────────────────────────────────────────────

class CreateTaskIn(BaseModel):
    title: str
    description:    Optional[str] = None
    priority:       str = "medium"
    due_date:       Optional[str] = None        # YYYY-MM-DD
    assignees:      Optional[list[str]] = None  # multi-assignee
    shared_with:    Optional[list[str]] = None
    parent_id:      Optional[str] = None         # for subtasks


class PatchTaskIn(BaseModel):
    title:          Optional[str] = None
    description:    Optional[str] = None
    priority:       Optional[str] = None
    status:         Optional[str] = None
    due_date:       Optional[str] = None
    assignees:      Optional[list[str]] = None
    shared_with:    Optional[list[str]] = None


# ─── List ───────────────────────────────────────────────────────────

@router.get("")
def list_my_tasks(db: Session = Depends(get_db),
                   current_user: dict = Depends(get_current_user),
                   include_closed: bool = False):
    """All top-level tasks where the caller is owner, assignee, or shared.
    Subtasks come nested inside each parent. Use ?include_closed=true to
    surface closed parents too."""
    me = _norm_email(current_user.get("email")) or ""
    q = db.query(PersonalTask).filter(PersonalTask.parent_id.is_(None))
    # Visibility: owner / any assignee / shared. JSON contains via LIKE
    # on the serialized list — SQLite-safe.
    q = q.filter(or_(
        PersonalTask.owner_email == me,
        PersonalTask.assignees.like(f'%"{me}"%'),
        PersonalTask.shared_with.like(f'%"{me}"%'),
    ))
    if not include_closed:
        q = q.filter(PersonalTask.status != "closed")
    parents = q.order_by(PersonalTask.due_date.asc().nullslast(),
                          PersonalTask.priority,
                          PersonalTask.created_at.desc()).all()

    # Bulk-load all subtasks for these parents
    parent_ids = [p.id for p in parents]
    subs_by_parent: dict = {}
    if parent_ids:
        subs = (db.query(PersonalTask)
                  .filter(PersonalTask.parent_id.in_(parent_ids)).all())
        for s in subs:
            subs_by_parent.setdefault(str(s.parent_id), []).append(s)

    return {
        "tasks": [_serialize(p, subtasks=subs_by_parent.get(str(p.id), []))
                    for p in parents],
    }


# ─── Create ─────────────────────────────────────────────────────────

@router.post("", status_code=201)
def create_task(payload: CreateTaskIn,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    me = _norm_email(current_user.get("email")) or ""
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="title is required")
    if payload.priority not in PRIORITIES:
        raise HTTPException(status_code=422,
                            detail=f"priority must be one of {PRIORITIES}")

    parent = None
    if payload.parent_id:
        parent = db.query(PersonalTask).filter(PersonalTask.id == payload.parent_id).first()
        if not parent or parent.parent_id is not None:
            raise HTTPException(status_code=422,
                                detail="parent_id must point at a top-level task")
        _ensure_visible(parent, me)

    due = None
    if payload.due_date:
        try:
            due = _date.fromisoformat(payload.due_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="due_date must be YYYY-MM-DD")

    shared    = [_norm_email(e) for e in (payload.shared_with or []) if _norm_email(e)]
    assignees = [_norm_email(e) for e in (payload.assignees   or []) if _norm_email(e)]
    t = PersonalTask(
        parent_id=parent.id if parent else None,
        owner_email=me,
        assignees=assignees,
        shared_with=shared,
        title=payload.title.strip(),
        description=(payload.description or "").strip() or None,
        priority=payload.priority,
        status="new",
        due_date=due,
        position=0,
        created_by=me,
        updated_by=me,
    )
    db.add(t); db.commit(); db.refresh(t)
    return _serialize(t)


# ─── Patch ──────────────────────────────────────────────────────────

@router.patch("/{task_id}")
def patch_task(task_id: str, payload: PatchTaskIn,
                db: Session = Depends(get_db),
                current_user: dict = Depends(get_current_user)):
    me = _norm_email(current_user.get("email")) or ""
    t = db.query(PersonalTask).filter(PersonalTask.id == task_id).first()
    _ensure_visible(t, me)

    # Permission tiers:
    #   - owner: anything (content + ownership fields + sharing list)
    #   - assignee / shared: content only (title, description, priority,
    #     due_date, status) — NOT assignee_email or shared_with.
    is_owner = (me == t.owner_email)
    can_edit_content = _can_edit_content(t, me)
    if not can_edit_content:
        raise HTTPException(status_code=403,
                            detail="you don't have edit access to this task")
    if not is_owner:
        # Block assignees / sharing changes for non-owners
        for k in ("assignees", "shared_with"):
            if getattr(payload, k) is not None:
                raise HTTPException(status_code=403,
                                    detail="only the owner can change assignees or sharing")

    data = payload.model_dump(exclude_unset=True)
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="title cannot be empty")
        t.title = title
    if "description" in data:
        t.description = (data["description"] or "").strip() or None
    if "priority" in data:
        if data["priority"] not in PRIORITIES:
            raise HTTPException(status_code=422,
                                detail=f"priority must be one of {PRIORITIES}")
        t.priority = data["priority"]
    if "due_date" in data:
        v = data["due_date"]
        if v in (None, ""):
            t.due_date = None
        else:
            try:
                t.due_date = _date.fromisoformat(v)
            except ValueError:
                raise HTTPException(status_code=422, detail="due_date must be YYYY-MM-DD")
    if "assignees" in data:
        t.assignees = [_norm_email(e) for e in (data["assignees"] or [])
                       if _norm_email(e)]
    if "shared_with" in data:
        t.shared_with = [_norm_email(e) for e in (data["shared_with"] or [])
                         if _norm_email(e)]
    if "status" in data:
        if data["status"] not in STATUSES:
            raise HTTPException(status_code=422,
                                detail=f"status must be one of {STATUSES}")
        new_status = data["status"]
        prior = t.status
        t.status = new_status
        if new_status == "closed":
            t.closed_at = datetime.utcnow()
            t.closed_by = me
        elif prior == "closed":
            # Re-opening
            t.closed_at = None
            t.closed_by = None
    t.updated_by = me
    db.commit(); db.refresh(t)

    # If a subtask just closed, maybe auto-close the parent
    if t.parent_id and t.status == "closed":
        _maybe_close_parent_if_all_subtasks_done(db, t.parent_id, by=me)
        db.commit()

    # Re-serialize with fresh subtask list if this is a parent
    children = (db.query(PersonalTask)
                  .filter(PersonalTask.parent_id == t.id).all()
                if t.parent_id is None else [])
    return _serialize(t, subtasks=children)


# ─── Delete ─────────────────────────────────────────────────────────

@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: str,
                 db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    me = _norm_email(current_user.get("email")) or ""
    t = db.query(PersonalTask).filter(PersonalTask.id == task_id).first()
    _ensure_visible(t, me)
    _ensure_can_delete(t, me)
    # Cascade subtasks manually (no SQLAlchemy relationship cascade)
    if t.parent_id is None:
        db.query(PersonalTask).filter(PersonalTask.parent_id == t.id).delete(
            synchronize_session=False)
    db.delete(t); db.commit()
    return
