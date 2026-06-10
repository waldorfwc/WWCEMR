"""Checklist API — daily task list + completion + admin."""
from __future__ import annotations

from datetime import date as _date, datetime, time as _time
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.checklist import TaskTemplate, TaskInstance, PainPoint
from app.models.groups import Group
from app.models.user import User, PRACTICE_ROLES
from app.routers.auth import get_current_user
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.services import checklist_service
from app.services import checklist_seed


router = APIRouter(prefix="/checklist", tags=["checklist"])


class CompletePayload(BaseModel):
    notes: Optional[str] = None


class SkipPayload(BaseModel):
    reason: str


class AnswerPayload(BaseModel):
    answer: str                       # "yes" | "no"
    followup_count: Optional[int] = None
    followup_text: Optional[str] = None


class PainPointPayload(BaseModel):
    body: str                         # the pain-point description
    occurred_on: Optional[str] = None # ISO date — defaults to today


class PainPointReviewPayload(BaseModel):
    status: str                       # "acknowledged" | "resolved"
    response: Optional[str] = None


class UserRolePayload(BaseModel):
    practice_role: Optional[str] = None
    phone_number: Optional[str] = None
    slack_user_id: Optional[str] = None
    notify_email: Optional[bool] = None
    notify_slack: Optional[bool] = None
    notify_sms: Optional[bool] = None


# ─── My daily list ────────────────────────────────────────────────────

@router.get("/my-today")
def my_today(date_str: Optional[str] = Query(None, alias="date"),
             db: Session = Depends(get_db),
             current_user: dict = Depends(get_current_user)):
    # Guard against bad date strings — fromisoformat raises ValueError
    # on malformed input which would otherwise propagate as a 500
    # instead of the more useful 422. (Fable cross-cutting audit #24.)
    try:
        target = _date.fromisoformat(date_str) if date_str else _date.today()
    except ValueError:
        raise HTTPException(status_code=422,
                             detail="date must be YYYY-MM-DD")
    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="not authenticated")

    user = db.query(User).filter(User.email == email).first()
    rows = checklist_service.my_today(db, email, target)
    counts = {"pending": 0, "in_progress": 0, "done": 0, "skipped": 0}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    return {
        "date": str(target),
        "user": {
            "email": email,
            "display_name": user.display_name if user else None,
            "practice_role": user.practice_role if user else None,
        },
        "counts": counts,
        "tasks": rows,
    }


def _assert_task_owner_or_manage(db: Session, instance_id: str,
                                    current_user: dict) -> TaskInstance:
    """Owner-or-Manage check shared by answer/skip/reopen — anyone with
    My Checklist:Manage can act on any task, owners can act on their own.
    Without this, any logged-in user used to answer or skip a
    colleague's accountability task. (Fable cross-cutting audit #22.)
    """
    from app.permissions.catalog import Module, Tier
    from app.permissions.resolver import effective_tier
    inst = db.query(TaskInstance).filter(TaskInstance.id == instance_id).first()
    if inst is None:
        raise HTTPException(status_code=404, detail="task instance not found")
    me_email = (current_user.get("email") or "").lower().strip()
    if (inst.assigned_to_email or "").lower().strip() != me_email:
        if effective_tier(db, me_email, Module.MY_CHECKLIST) < Tier.MANAGE:
            raise HTTPException(
                status_code=403,
                detail="Only the task owner or a My Checklist:Manage user "
                       "can act on this task.")
    return inst


@router.post("/instances/{instance_id}/answer")
def answer(instance_id: str, payload: AnswerPayload,
           db: Session = Depends(get_db),
           current_user: dict = Depends(get_current_user)):
    """Answer a checklist task Yes or No. When No is given, the task's
    follow-up question (count or reason) must be provided too."""
    _assert_task_owner_or_manage(db, instance_id, current_user)
    try:
        inst = checklist_service.record_answer(
            db, instance_id,
            by_email=current_user.get("email"),
            answer=payload.answer,
            followup_count=payload.followup_count,
            followup_text=payload.followup_text,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "id": str(inst.id), "status": inst.status,
        "answer": inst.answer,
        "followup_count": inst.followup_count,
        "followup_text": inst.followup_text,
        "completed_at": str(inst.completed_at) if inst.completed_at else None,
        "completed_by": inst.completed_by,
    }


@router.post("/instances/{instance_id}/skip")
def skip(instance_id: str, payload: SkipPayload,
         db: Session = Depends(get_db),
         current_user: dict = Depends(get_current_user)):
    _assert_task_owner_or_manage(db, instance_id, current_user)
    inst = checklist_service.mark_skipped(db, instance_id,
                                          by_email=current_user.get("email"),
                                          reason=payload.reason)
    return {"id": str(inst.id), "status": inst.status,
            "skipped_reason": inst.skipped_reason}


@router.post("/instances/{instance_id}/reopen")
def reopen(instance_id: str, db: Session = Depends(get_db),
           current_user: dict = Depends(get_current_user)):
    """Re-open a done/skipped task. A user can re-open their OWN task at any
    time; re-opening someone else's requires My Checklist:Manage."""
    from app.models.checklist import TaskInstance
    from app.permissions.catalog import Module, Tier
    from app.permissions.resolver import effective_tier
    inst = db.query(TaskInstance).filter(TaskInstance.id == instance_id).first()
    if inst is None:
        raise HTTPException(status_code=404, detail="task instance not found")
    me_email = (current_user.get("email") or "").lower().strip()
    if (inst.assigned_to_email or "").lower().strip() != me_email:
        if effective_tier(db, me_email, Module.MY_CHECKLIST) < Tier.MANAGE:
            raise HTTPException(
                status_code=403,
                detail="Only the task owner or a My Checklist:Manage user "
                       "can re-open this task.")
    inst = checklist_service.reopen(db, instance_id)
    return {"id": str(inst.id), "status": inst.status}


# ─── User self-service: set my role + notification prefs ──────────────

@router.get("/me")
def get_me(db: Session = Depends(get_db),
           current_user: dict = Depends(get_current_user)):
    email = current_user.get("email")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not in directory")
    return {
        "email": user.email,
        "display_name": user.display_name,
        "group": user.group.value if user.group else None,
        "practice_role": user.practice_role,
        "phone_number": user.phone_number,
        "slack_user_id": user.slack_user_id,
        "notify_email": user.notify_email,
        "notify_slack": user.notify_slack,
        "notify_sms": user.notify_sms,
    }


@router.patch("/me")
def update_me(payload: UserRolePayload, db: Session = Depends(get_db),
              current_user: dict = Depends(get_current_user)):
    """Self-service update of notification preferences only.

    Practice role is assigned by an admin via /admin/users — users cannot
    change their own role. Any practice_role value sent here is ignored.
    """
    email = current_user.get("email")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not in directory")
    for f in ("phone_number", "slack_user_id", "notify_email", "notify_slack", "notify_sms"):
        v = getattr(payload, f)
        if v is not None:
            setattr(user, f, v)
    db.commit(); db.refresh(user)
    return {"updated": True, "practice_role": user.practice_role}


@router.get("/roles")
def list_roles(current_user: dict = Depends(get_current_user)):
    """Available practice roles."""
    return {"roles": list(PRACTICE_ROLES)}


# ─── Admin: templates CRUD + seeding + manual generation ──────────────
# All template-mutation endpoints require checklist:manage. The list/get
# endpoints stay open to any authenticated user (used by the My Profile
# preview and read-only contexts).

CATEGORIES = ["clinical", "admin", "billing", "safety", "compliance", "communication"]
FREQUENCIES = ["daily", "weekly", "monthly", "quarterly", "annual", "on_demand"]
PRIORITIES = ["low", "medium", "high", "critical"]
RECURRENCE_KINDS = [
    "daily", "weekdays_of_week", "days_of_month", "anniversary",
    "every_n_days", "every_n_months", "every_n_years", "on_demand",
]
WEEKEND_RULES = ["skip", "roll_to_monday"]
FOLLOWUP_KINDS = ["none", "count", "reason"]
EXPIRES_KINDS = ["never", "days", "weeks", "months", "years", "specific_date"]


class TemplatePayload(BaseModel):
    title: str
    description: Optional[str] = None
    instructions: Optional[str] = None
    category: str
    # Legacy frequency triple — still accepted; new clients should send
    # recurrence_kind + the matching detail field.
    frequency: str = "daily"
    weekday: Optional[int] = None
    day_of_month: Optional[int] = None
    # New flexible recurrence
    recurrence_kind: Optional[str] = None
    recurrence_weekdays: Optional[List[int]] = None
    recurrence_days_of_month: Optional[List[int]] = None
    anchor_date: Optional[str] = None        # ISO date
    interval_n: Optional[int] = None
    weekend_rule: Optional[str] = None       # skip | roll_to_monday
    due_time: Optional[str] = None
    priority: str = "medium"
    active: bool = True
    # Yes/No question
    question_text: Optional[str] = None
    followup_kind: str = "none"
    followup_prompt: Optional[str] = None
    # Manager escalation
    escalate_to_email: Optional[str] = None
    escalate_after_hours: Optional[int] = None
    # Training prerequisite
    requires_training: Optional[bool] = None
    training_material_url: Optional[str] = None
    expires_kind: Optional[str] = None
    expires_value: Optional[int] = None
    expires_on_date: Optional[str] = None       # ISO date
    # Targeting
    role: Optional[str] = None
    assigned_group_ids: List[str] = []
    assigned_users: List[str] = []
    assigned_permission: Optional[str] = None


def _template_to_dict(t: TaskTemplate) -> dict:
    return {
        "id": str(t.id),
        "title": t.title, "description": t.description, "instructions": t.instructions,
        "role": t.role, "category": t.category,
        "frequency": t.frequency,
        "weekday": t.weekday, "day_of_month": t.day_of_month,
        "recurrence_kind": t.recurrence_kind,
        "recurrence_weekdays": t.recurrence_weekdays,
        "recurrence_days_of_month": t.recurrence_days_of_month,
        "anchor_date": str(t.anchor_date) if t.anchor_date else None,
        "interval_n": t.interval_n,
        "weekend_rule": t.weekend_rule,
        "due_time": str(t.due_time) if t.due_time else None,
        "priority": t.priority, "active": t.active,
        "question_text": t.question_text,
        "followup_kind": t.followup_kind or "none",
        "followup_prompt": t.followup_prompt,
        "escalate_to_email": t.escalate_to_email,
        "escalate_after_hours": t.escalate_after_hours,
        "requires_training": bool(t.requires_training),
        "training_material_url": t.training_material_url,
        "expires_kind": t.expires_kind or "never",
        "expires_value": t.expires_value,
        "expires_on_date": str(t.expires_on_date) if t.expires_on_date else None,
        "assigned_groups": [{"id": g.id, "name": g.name} for g in (t.assigned_groups or [])],
        "assigned_users": list(t.assigned_users or []),
        "assigned_permission": t.assigned_permission,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "created_by": t.created_by,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "updated_by": t.updated_by,
    }


def _validate_template_fields(payload: TemplatePayload, db: Session) -> None:
    if payload.category not in CATEGORIES:
        raise HTTPException(status_code=422, detail=f"category must be one of {CATEGORIES}")
    if payload.frequency not in FREQUENCIES:
        raise HTTPException(status_code=422, detail=f"frequency must be one of {FREQUENCIES}")
    if payload.priority not in PRIORITIES:
        raise HTTPException(status_code=422, detail=f"priority must be one of {PRIORITIES}")
    if payload.followup_kind not in FOLLOWUP_KINDS:
        raise HTTPException(status_code=422,
                            detail=f"followup_kind must be one of {FOLLOWUP_KINDS}")
    if payload.weekend_rule and payload.weekend_rule not in WEEKEND_RULES:
        raise HTTPException(status_code=422,
                            detail=f"weekend_rule must be one of {WEEKEND_RULES}")
    if payload.recurrence_kind:
        if payload.recurrence_kind not in RECURRENCE_KINDS:
            raise HTTPException(status_code=422,
                                detail=f"recurrence_kind must be one of {RECURRENCE_KINDS}")
        rk = payload.recurrence_kind
        if rk == "weekdays_of_week" and not payload.recurrence_weekdays:
            raise HTTPException(status_code=422,
                                detail="recurrence_weekdays required for weekdays_of_week")
        if rk == "days_of_month" and not payload.recurrence_days_of_month:
            raise HTTPException(status_code=422,
                                detail="recurrence_days_of_month required for days_of_month")
        if rk in ("anniversary", "every_n_days", "every_n_months", "every_n_years"):
            if not payload.anchor_date:
                raise HTTPException(status_code=422,
                                    detail=f"anchor_date required for {rk}")
        if rk in ("every_n_days", "every_n_months", "every_n_years"):
            if not payload.interval_n or payload.interval_n < 1:
                raise HTTPException(status_code=422,
                                    detail=f"interval_n (>=1) required for {rk}")
    if payload.followup_kind != "none" and not (payload.followup_prompt or "").strip():
        raise HTTPException(status_code=422,
                            detail="followup_prompt required when followup_kind != 'none'")
    mgr = (payload.escalate_to_email or "").lower().strip()
    if not mgr:
        raise HTTPException(status_code=422,
                            detail="A manager (escalate_to_email) is required on every template")
    u = db.query(User).filter(User.email == mgr).first()
    if u is None:
        raise HTTPException(status_code=422,
                            detail=f"manager not found: {payload.escalate_to_email}")
    if payload.expires_kind and payload.expires_kind not in EXPIRES_KINDS:
        raise HTTPException(status_code=422,
                            detail=f"expires_kind must be one of {EXPIRES_KINDS}")
    if payload.expires_kind in ("days", "weeks", "months", "years"):
        if not payload.expires_value or payload.expires_value < 1:
            raise HTTPException(status_code=422,
                                detail=f"expires_value (>=1) required for {payload.expires_kind}")
    if payload.expires_kind == "specific_date" and not payload.expires_on_date:
        raise HTTPException(status_code=422,
                            detail="expires_on_date required for specific_date expiration")
    if payload.frequency == "weekly" and payload.weekday is None and not payload.recurrence_kind:
        raise HTTPException(status_code=422, detail="weekday required for weekly frequency")
    if payload.frequency == "monthly" and payload.day_of_month is None and not payload.recurrence_kind:
        raise HTTPException(status_code=422, detail="day_of_month required for monthly frequency")
    # assigned_permission is a deprecated targeting field — kept on the
    # column for backwards compat but no longer validated against any
    # catalog (the legacy PERMISSIONS catalog was removed in Phase 4 of
    # the permissions redesign).
    if payload.assigned_group_ids:
        found = db.query(Group).filter(Group.id.in_(payload.assigned_group_ids)).all()
        if len(found) != len(payload.assigned_group_ids):
            missing = set(payload.assigned_group_ids) - {g.id for g in found}
            raise HTTPException(status_code=422,
                                detail=f"unknown group id(s): {sorted(missing)}")


def _parse_due_time(s: Optional[str]) -> Optional[_time]:
    if not s:
        return None
    parts = s.split(":")
    h = int(parts[0]); m = int(parts[1]) if len(parts) > 1 else 0
    sec = int(parts[2]) if len(parts) > 2 else 0
    return _time(h, m, sec)


def _parse_iso_date(s: Optional[str]) -> Optional[_date]:
    if not s:
        return None
    return _date.fromisoformat(s)


@router.get("/templates")
def list_templates(db: Session = Depends(get_db),
                   role: Optional[str] = None,
                   group_id: Optional[str] = None,
                   active: Optional[bool] = None,
                   include_assignees: bool = False,
                   current_user: dict = Depends(get_current_user)):
    q = db.query(TaskTemplate)
    if role:
        q = q.filter(TaskTemplate.role == role)
    if active is not None:
        q = q.filter(TaskTemplate.active.is_(active))
    rows = q.order_by(TaskTemplate.category, TaskTemplate.due_time, TaskTemplate.title).all()
    if group_id:
        rows = [t for t in rows if any(g.id == group_id for g in (t.assigned_groups or []))]

    # When include_assignees=true, resolve who would actually receive each
    # template today (after group expansion + permission filter + training
    # gate + active-user filter). Useful for the admin templates list to
    # show counts + flag templates with zero assignees.
    out = [_template_to_dict(t) for t in rows]
    if include_assignees:
        ASSIGNEE_CAP = 25
        # Resolve assignees per template. Each _assignees_for_template
        # call already does its own group expansion and runs a single
        # User query — the cost is small per template but adds up on a
        # router with dozens of templates. The internal-call cost is
        # bounded by the unique-email set after group expansion.
        # (Fable cross-cutting audit #14 — kept the loop, but the
        # batch resolver `effective_tier_for_users` is available for
        # callers that need to gate by tier.)
        for t, d in zip(rows, out):
            users = checklist_service._assignees_for_template(db, t)
            d["assignee_count"] = len(users)
            d["assignee_emails"] = sorted(u.email for u in users[:ASSIGNEE_CAP])
            d["assignee_truncated"] = len(users) > ASSIGNEE_CAP

    return {"templates": out}


@router.get("/templates/{template_id}")
def get_template(template_id: str, db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    t = db.query(TaskTemplate).filter(TaskTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    return _template_to_dict(t)


@router.post("/templates", status_code=201)
def create_template(payload: TemplatePayload,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.MY_CHECKLIST, Tier.MANAGE))):
    _validate_template_fields(payload, db)
    t = TaskTemplate(
        title=payload.title.strip(),
        description=payload.description,
        instructions=payload.instructions,
        category=payload.category,
        frequency=payload.frequency,
        weekday=payload.weekday,
        day_of_month=payload.day_of_month,
        recurrence_kind=payload.recurrence_kind,
        recurrence_weekdays=payload.recurrence_weekdays,
        recurrence_days_of_month=payload.recurrence_days_of_month,
        anchor_date=_parse_iso_date(payload.anchor_date),
        interval_n=payload.interval_n,
        weekend_rule=payload.weekend_rule,
        due_time=_parse_due_time(payload.due_time),
        priority=payload.priority,
        active=payload.active,
        question_text=(payload.question_text or "").strip() or None,
        followup_kind=payload.followup_kind or "none",
        followup_prompt=(payload.followup_prompt or "").strip() or None,
        escalate_to_email=(payload.escalate_to_email or "").lower().strip() or None,
        escalate_after_hours=payload.escalate_after_hours or 24,
        requires_training=True if payload.requires_training is None else payload.requires_training,
        training_material_url=(payload.training_material_url or "").strip() or None,
        expires_kind=payload.expires_kind or "never",
        expires_value=payload.expires_value,
        expires_on_date=_parse_iso_date(payload.expires_on_date),
        role=payload.role or "custom",
        assigned_users=[e.lower().strip() for e in payload.assigned_users if e.strip()] or None,
        assigned_permission=payload.assigned_permission,
        created_by=current_user.get("email") or "system",
        updated_by=current_user.get("email") or "system",
    )
    db.add(t); db.flush()
    if payload.assigned_group_ids:
        groups = db.query(Group).filter(Group.id.in_(payload.assigned_group_ids)).all()
        t.assigned_groups = groups
    _auto_grant_super_admin_trainer(db, t.id,
                                       authorized_by=current_user.get("email") or "system")
    db.commit(); db.refresh(t)
    return _template_to_dict(t)


def _auto_grant_super_admin_trainer(db: Session, template_id, *, authorized_by: str):
    """Every Super Admin user is automatically authorized to train others
    on every template — present and future. Idempotent: skips if a
    (user, template) authorization already exists, including a revoked one
    (so manual revocations are preserved)."""
    from app.models.training import TrainerAuthorization
    from app.models.groups import Group
    grp = db.query(Group).filter(Group.name == "Super Admin").first()
    if not grp:
        return
    for u in (grp.members or []):
        email = (u.email or "").lower().strip()
        if not email:
            continue
        existing = (db.query(TrainerAuthorization)
                      .filter(TrainerAuthorization.user_email == email,
                              TrainerAuthorization.template_id == template_id)
                      .first())
        if existing:
            continue
        db.add(TrainerAuthorization(
            user_email=email,
            template_id=template_id,
            authorized_by=authorized_by,
            notes="Auto-granted: Super Admin is a trainer for every template",
        ))


@router.patch("/templates/{template_id}")
def update_template(template_id: str, payload: TemplatePayload,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.MY_CHECKLIST, Tier.MANAGE))):
    t = db.query(TaskTemplate).filter(TaskTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    _validate_template_fields(payload, db)
    t.title = payload.title.strip()
    t.description = payload.description
    t.instructions = payload.instructions
    t.category = payload.category
    t.frequency = payload.frequency
    t.weekday = payload.weekday
    t.day_of_month = payload.day_of_month
    t.recurrence_kind = payload.recurrence_kind
    t.recurrence_weekdays = payload.recurrence_weekdays
    t.recurrence_days_of_month = payload.recurrence_days_of_month
    t.anchor_date = _parse_iso_date(payload.anchor_date)
    t.interval_n = payload.interval_n
    t.weekend_rule = payload.weekend_rule
    t.due_time = _parse_due_time(payload.due_time)
    t.priority = payload.priority
    t.active = payload.active
    t.question_text = (payload.question_text or "").strip() or None
    t.followup_kind = payload.followup_kind or "none"
    t.followup_prompt = (payload.followup_prompt or "").strip() or None
    t.escalate_to_email = (payload.escalate_to_email or "").lower().strip() or None
    if payload.escalate_after_hours is not None:
        t.escalate_after_hours = payload.escalate_after_hours
    if payload.requires_training is not None:
        t.requires_training = payload.requires_training
    t.training_material_url = (payload.training_material_url or "").strip() or None
    if payload.expires_kind is not None:
        t.expires_kind = payload.expires_kind
    t.expires_value = payload.expires_value
    t.expires_on_date = _parse_iso_date(payload.expires_on_date)
    t.role = payload.role or t.role or "custom"
    t.assigned_users = [e.lower().strip() for e in payload.assigned_users if e.strip()] or None
    t.assigned_permission = payload.assigned_permission
    if payload.assigned_group_ids is not None:
        groups = db.query(Group).filter(Group.id.in_(payload.assigned_group_ids)).all()
        t.assigned_groups = groups
    t.updated_by = current_user.get("email") or "system"
    db.commit(); db.refresh(t)
    return _template_to_dict(t)


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(template_id: str,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.MY_CHECKLIST, Tier.MANAGE))):
    t = db.query(TaskTemplate).filter(TaskTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    db.delete(t); db.commit()
    return None


@router.post("/templates/{template_id}/preview-assignees")
def preview_assignees(template_id: str,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(get_current_user)):
    """Show who would receive this template if generated today.

    Useful in the admin UI to sanity-check targeting before saving.
    """
    t = db.query(TaskTemplate).filter(TaskTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    users = checklist_service._assignees_for_template(db, t)
    return {
        "count": len(users),
        "assignees": sorted(u.email for u in users),
    }


@router.post("/seed")
def seed_templates(db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.MY_CHECKLIST, Tier.MANAGE))):
    """Idempotently seeds the Phase A template library."""
    return checklist_seed.seed(db)


@router.post("/generate-for-today")
def generate_today(date_str: Optional[str] = Query(None, alias="date"),
                   db: Session = Depends(get_db),
                   current_user: dict = Depends(requires_tier(Module.MY_CHECKLIST, Tier.MANAGE))):
    """Manually trigger task-instance generation for a given date. Defaults
    to today. The midnight cron handles the normal case; this is for
    testing and for backfilling a date the cron missed."""
    try:
        target = _date.fromisoformat(date_str) if date_str else _date.today()
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    return checklist_service.generate_instances_for_date(db, target)


# ─── Manager dashboard ───────────────────────────────────────────────
# Aggregates everything a manager needs to see:
#   - Tasks where the user answered No (with the follow-up text/count)
#   - Overdue/uncompleted tasks past their escalate_after_hours window
#   - Open pain points from anyone whose template lists this user as
#     escalate_to_email (their direct reports, effectively)

@router.get("/manager/dashboard")
def manager_dashboard(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: dict = Depends(requires_tier(Module.MY_CHECKLIST, Tier.MANAGE)),
):
    """Manager view of accountability state across direct reports.

    A "direct report" is anyone whose task template lists the current
    user as escalate_to_email. Pain points are filtered to that same set
    of direct reports.
    """
    from datetime import timedelta
    me = (current_user.get("email") or "").lower().strip()
    cutoff = _date.today() - timedelta(days=days)

    # All instances tied to my templates in the window
    rows = (db.query(TaskInstance, TaskTemplate)
              .join(TaskTemplate, TaskInstance.template_id == TaskTemplate.id)
              .filter(
                  TaskTemplate.escalate_to_email == me,
                  TaskInstance.due_date >= cutoff,
              ).order_by(TaskInstance.due_date.desc()).all())

    no_answers = []
    overdue = []
    direct_reports: set[str] = set()
    now = datetime.utcnow()
    for inst, tmpl in rows:
        direct_reports.add(inst.assigned_to_email)
        if inst.answer == "no":
            no_answers.append({
                "instance_id": str(inst.id),
                "task": tmpl.question_text or tmpl.title,
                "owner": inst.assigned_to_email,
                "due_date": str(inst.due_date),
                "followup_kind": tmpl.followup_kind,
                "followup_count": inst.followup_count,
                "followup_text": inst.followup_text,
                "answered_at": str(inst.completed_at) if inst.completed_at else None,
            })
        # Overdue: still pending past escalate window. done + skipped both
        # represent a resolved instance and must not surface here.
        if inst.status not in ("done", "skipped"):
            base = inst.due_at or datetime.combine(inst.due_date, datetime.min.time())
            eligible_at = base + timedelta(hours=tmpl.escalate_after_hours or 24)
            if now >= eligible_at:
                overdue.append({
                    "instance_id": str(inst.id),
                    "task": tmpl.question_text or tmpl.title,
                    "owner": inst.assigned_to_email,
                    "due_date": str(inst.due_date),
                    "due_at": str(inst.due_at) if inst.due_at else None,
                    "hours_late": int((now - eligible_at).total_seconds() // 3600),
                    "escalation_sent_at": str(inst.escalation_sent_at)
                                          if inst.escalation_sent_at else None,
                })

    # Open pain points from direct reports (status new + in_progress).
    # The primary triage surface is now the pain-point owner's My Checklist,
    # but each manager can still see them on their own dashboard.
    pp_rows = []
    if direct_reports:
        pp_rows = (db.query(PainPoint)
                     .filter(PainPoint.user_email.in_(direct_reports),
                             PainPoint.status.in_(["new", "in_progress"]),
                             PainPoint.occurred_on >= cutoff)
                     .order_by(PainPoint.occurred_on.desc())
                     .all())

    # Unassigned templates the manager owns + (for super admins) any
    # orphan template (active, requires_training or not, with no
    # escalate_to_email and zero assignees). A template with zero
    # assignees never generates instances — it's silently broken.
    # Super Admins (the cross-module system role) can see orphan templates
    # so they can re-assign or clean them up.
    from app.models.user import User
    me_row = db.query(User).filter(User.email == me).first()
    is_super_admin = bool(me_row and me_row.is_super_admin)

    unassigned = []
    candidate_templates = (db.query(TaskTemplate)
                             .filter(TaskTemplate.active.is_(True))
                             .all())
    for t in candidate_templates:
        owns = (t.escalate_to_email == me)
        is_orphan = (t.escalate_to_email is None and is_super_admin)
        if not (owns or is_orphan):
            continue
        users = checklist_service._assignees_for_template(db, t)
        if users:
            continue
        # Diagnose why no one matches — helps the admin fix it
        reasons = []
        if not (t.assigned_groups or t.assigned_users or t.assigned_permission or t.role):
            reasons.append("no targets configured")
        elif t.requires_training:
            reasons.append("no one is trained on this task")
        else:
            reasons.append("no active users match the targeting")
        unassigned.append({
            "id": str(t.id),
            "title": t.title,
            "question_text": t.question_text,
            "category": t.category,
            "escalate_to_email": t.escalate_to_email,
            "owner_role": "you" if owns else "orphan",
            "reasons": reasons,
        })

    return {
        "window_days": days,
        "direct_reports": sorted(direct_reports),
        "no_answers": no_answers,
        "overdue": overdue,
        "pain_points_open": [_painpoint_to_dict(p) for p in pp_rows],
        "unassigned_templates": unassigned,
    }


@router.post("/manager/run-escalations")
def run_escalations(db: Session = Depends(get_db),
                    current_user: dict = Depends(requires_tier(Module.MY_CHECKLIST, Tier.MANAGE))):
    """Manually fire the escalation sweep. Normally runs hourly via the
    scheduler, but useful for testing the email/slack flow."""
    from app.services import checklist_notifications
    return checklist_notifications.run_escalation_sweep(db)


# ─── Pain points ─────────────────────────────────────────────────────
# Logged at the end (or start) of a user's checklist. Visible to the
# user's manager(s) on the manager dashboard.

def _painpoint_to_dict(p: PainPoint) -> dict:
    return {
        "id": str(p.id),
        "user_email": p.user_email,
        "occurred_on": str(p.occurred_on),
        "body": p.body,
        "status": p.status,
        "reviewed_by": p.reviewed_by,
        "reviewed_at": str(p.reviewed_at) if p.reviewed_at else None,
        "response": p.response,
        "acknowledged_at": str(p.acknowledged_at) if p.acknowledged_at else None,
        "created_at": str(p.created_at),
    }


# Pain points have a single practice-wide owner who triages every
# submission. Configurable via PracticeConfig (key `pain_point_owner_email`);
# defaults to Oliver Cooke.
PAIN_POINT_OWNER_KEY = "pain_point_owner_email"
DEFAULT_PAIN_POINT_OWNER = "ocooke@waldorfwomenscare.com"


def _pain_point_owner_email(db: Session) -> str:
    from app.models.practice_config import get_setting
    return (get_setting(db, PAIN_POINT_OWNER_KEY)
              or DEFAULT_PAIN_POINT_OWNER).lower().strip()


def _is_pain_point_owner(db: Session, email: Optional[str]) -> bool:
    if not email:
        return False
    return email.lower().strip() == _pain_point_owner_email(db)


@router.post("/pain-points", status_code=201)
def submit_pain_point(payload: PainPointPayload,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(get_current_user)):
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="body required")
    try:
        occurred = _date.fromisoformat(payload.occurred_on) if payload.occurred_on else _date.today()
    except ValueError:
        raise HTTPException(status_code=422,
                             detail="occurred_on must be YYYY-MM-DD")
    submitter_email = (current_user.get("email") or "").lower().strip()
    p = PainPoint(
        user_email=submitter_email,
        occurred_on=occurred,
        body=body,
        status="new",
    )
    db.add(p); db.commit(); db.refresh(p)

    # Email the practice-wide pain-point owner.
    try:
        _notify_owner_new_pain_point(db, submitter=submitter_email,
                                       submitter_name=current_user.get("display_name"),
                                       body=body, occurred_on=occurred)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Pain-point owner-notify failed for %s: %s", submitter_email, exc)
    return _painpoint_to_dict(p)


def _notify_owner_new_pain_point(db: Session, *, submitter: str,
                                    submitter_name: Optional[str],
                                    body: str, occurred_on) -> None:
    from html import escape as _esc
    from app.services.checklist_notifications import send_email
    owner_email = _pain_point_owner_email(db)
    owner = db.query(User).filter(User.email == owner_email).first()
    if not owner or not owner.is_active or not owner.notify_email:
        return
    who = submitter_name or submitter.split("@")[0]
    subject = f"WWC · Pain point from {who}"
    # HTML-escape every user-controlled value so a body containing
    # `<script>` (or PHI styled as raw HTML) can't execute / be
    # rendered as markup in the email client. (Fable cross-cutting
    # audit #7.) Pain point bodies routinely contain PHI ("Mrs. Jones
    # was upset that..."); escaping is the minimum-necessary
    # protection. (We still email the body — switching to a link-only
    # email is a separate UX call.)
    body_html = (
        f"<p><strong>{_esc(who)}</strong> logged a pain point on "
        f"{_esc(occurred_on.isoformat())}:</p>"
        f"<blockquote style='border-left:3px solid #7B2D5E; padding-left:8px; "
        f"color:#444; margin:8px 0;'>{_esc(body)}</blockquote>"
        f"<p><a href='https://gw.waldorfwomenscare.com/my-checklist' "
        f"style='color:#7B2D5E'>Open My Checklist →</a></p>"
    )
    body_text = (
        f"{who} logged a pain point on {occurred_on.isoformat()}:\n\n"
        f"{body}\n\n"
        f"Open My Checklist: https://gw.waldorfwomenscare.com/my-checklist"
    )
    send_email(owner.email, subject, body_html, body_text)


def _notify_submitter_of_response(db: Session, *, pp: PainPoint) -> None:
    """Email the submitter that the owner has responded."""
    from html import escape as _esc
    from app.services.checklist_notifications import send_email
    u = db.query(User).filter(User.email == pp.user_email).first()
    if not u or not u.is_active or not u.notify_email:
        return
    who = u.display_name or (u.email or '').split('@')[0]
    subject = "WWC · Response to your pain point"
    # See _notify_owner_new_pain_point — all user-controlled fields
    # HTML-escaped before interpolation. (Fable cross-cutting #7.)
    body_html = (
        f"<p>Hi {_esc(who)},</p>"
        f"<p>Your pain point from <strong>{_esc(str(pp.occurred_on))}</strong> has a response:</p>"
        f"<blockquote style='border-left:3px solid #999; padding-left:8px; color:#444;'>"
        f"<em>Your pain point:</em><br/>{_esc(pp.body or '')}</blockquote>"
        f"<blockquote style='border-left:3px solid #7B2D5E; padding-left:8px; color:#444;'>"
        f"<em>Response from {_esc(pp.reviewed_by or 'owner')}:</em><br/>{_esc(pp.response or '')}"
        f"</blockquote>"
        f"<p><a href='https://gw.waldorfwomenscare.com/my-checklist' "
        f"style='color:#7B2D5E'>Open My Checklist →</a> to acknowledge.</p>"
    )
    body_text = (
        f"Your pain point from {pp.occurred_on} has a response.\n\n"
        f"Your pain point:\n{pp.body}\n\n"
        f"Response from {pp.reviewed_by or 'owner'}:\n{pp.response or ''}\n\n"
        f"Acknowledge at https://gw.waldorfwomenscare.com/my-checklist"
    )
    send_email(u.email, subject, body_html, body_text)


@router.get("/pain-points/mine")
def list_my_pain_points(db: Session = Depends(get_db),
                        current_user: dict = Depends(get_current_user)):
    """Pain points the current user submitted. Surfaces responses awaiting
    acknowledgement so the submitter's My Checklist can prompt them."""
    me = (current_user.get("email") or "").lower().strip()
    rows = (db.query(PainPoint)
              .filter(PainPoint.user_email == me)
              .order_by(PainPoint.occurred_on.desc(), PainPoint.created_at.desc())
              .all())
    return {"pain_points": [_painpoint_to_dict(p) for p in rows]}


@router.get("/pain-points/owner-queue")
def pain_point_owner_queue(db: Session = Depends(get_db),
                            current_user: dict = Depends(get_current_user)):
    """Every non-completed pain point in the practice. Visible only to the
    configured pain-point owner."""
    me = (current_user.get("email") or "").lower().strip()
    if not _is_pain_point_owner(db, me):
        # Quietly return empty so any non-owner caller gets a clean state
        return {"pain_points": [], "is_owner": False}
    rows = (db.query(PainPoint)
              .filter(PainPoint.status != "completed")
              .order_by(PainPoint.created_at.desc())
              .all())
    return {"pain_points": [_painpoint_to_dict(p) for p in rows],
            "is_owner": True}


class PainPointRespondPayload(BaseModel):
    response: str


@router.post("/pain-points/{pp_id}/respond")
def respond_to_pain_point(pp_id: str, payload: PainPointRespondPayload,
                            db: Session = Depends(get_db),
                            current_user: dict = Depends(get_current_user)):
    """Owner adds a comment; pain point moves to 'in_progress' and the
    submitter is emailed."""
    me = (current_user.get("email") or "").lower().strip()
    if not _is_pain_point_owner(db, me):
        raise HTTPException(status_code=403,
                            detail="only the pain-point owner can respond")
    body = (payload.response or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="response body is required")
    p = db.query(PainPoint).filter(PainPoint.id == pp_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="pain point not found")
    p.response = body
    p.reviewed_by = me
    p.reviewed_at = datetime.utcnow()
    p.status = "in_progress"
    # Submitter must re-acknowledge after a new response
    p.acknowledged_at = None
    db.commit(); db.refresh(p)

    try:
        _notify_submitter_of_response(db, pp=p)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Pain-point response-notify failed for %s: %s", p.user_email, exc)
    return _painpoint_to_dict(p)


@router.post("/pain-points/{pp_id}/acknowledge-response")
def acknowledge_pain_point_response(pp_id: str,
                                       db: Session = Depends(get_db),
                                       current_user: dict = Depends(get_current_user)):
    """Submitter confirms they saw the owner's response. Flips status to
    'completed'."""
    me = (current_user.get("email") or "").lower().strip()
    p = db.query(PainPoint).filter(PainPoint.id == pp_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="pain point not found")
    if (p.user_email or "").lower().strip() != me:
        raise HTTPException(status_code=403,
                            detail="only the submitter can acknowledge")
    if p.status != "in_progress":
        raise HTTPException(status_code=409,
                            detail=f"pain point is {p.status}, not in_progress")
    p.acknowledged_at = datetime.utcnow()
    p.status = "completed"
    db.commit(); db.refresh(p)
    return _painpoint_to_dict(p)
