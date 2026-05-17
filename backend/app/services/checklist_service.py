"""Checklist core: instance generation, completion, daily summary."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional, Set

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.checklist import TaskTemplate, TaskInstance
from app.models.user import User


def _assignees_for_template(db: Session, tmpl: TaskTemplate) -> List[User]:
    """Resolve the set of users a template should generate instances for.

    Phase 4 — union of three sources:
      1. assigned_groups → all members of any listed group
      2. assigned_users  → explicit list of emails (JSON)
      3. assigned_permission → any user whose effective_permissions include it

    De-duplicates by email. Falls back to legacy `role` field when none of
    the new fields are populated, so pre-migration templates still work.
    """
    from app.services.permissions import effective_permissions

    emails: Set[str] = set()

    for grp in (tmpl.assigned_groups or []):
        for u in grp.members:
            emails.add(u.email)

    for em in (tmpl.assigned_users or []):
        em = (em or "").lower().strip()
        if em:
            emails.add(em)

    if tmpl.assigned_permission:
        perm = tmpl.assigned_permission
        for u in db.query(User).all():
            if perm in effective_permissions(u):
                emails.add(u.email)

    # Legacy fallback — only used if none of the new fields are populated
    if not emails and tmpl.role:
        for u in db.query(User).filter(User.practice_role == tmpl.role).all():
            emails.add(u.email)

    if not emails:
        return []

    # Training gate (Phase 6): when the template requires training,
    # restrict assignees to users with an active, unexpired certification.
    if tmpl.requires_training:
        from app.services.training_service import certified_emails_for
        certified = certified_emails_for(db, tmpl.id)
        emails = emails & certified

    if not emails:
        return []
    # Active-user gate (Phase 7): suspended users don't generate tasks.
    return (db.query(User)
              .filter(User.email.in_(emails),
                      User.is_active.is_(True))
              .all())


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # 5=Sat, 6=Sun


def _resolve_recurrence_kind(tmpl: TaskTemplate) -> str:
    """Map legacy `frequency` rows to the new `recurrence_kind` if a row
    pre-dates the migration. Templates created post-migration set
    recurrence_kind directly."""
    if tmpl.recurrence_kind:
        return tmpl.recurrence_kind
    f = (tmpl.frequency or "daily").lower()
    if f == "daily":      return "daily"
    if f == "weekly":     return "weekdays_of_week"
    if f == "monthly":    return "days_of_month"
    if f == "annual":     return "anniversary"
    if f == "on_demand":  return "on_demand"
    return "daily"


def _template_fires_on(tmpl: TaskTemplate, d: date) -> bool:
    """True iff this template should produce an instance on date d
    (before the weekend rule is applied)."""
    kind = _resolve_recurrence_kind(tmpl)

    if kind == "on_demand":
        return False
    if kind == "daily":
        return True
    if kind == "weekdays_of_week":
        wd = tmpl.recurrence_weekdays
        if wd is None and tmpl.weekday is not None:
            wd = [tmpl.weekday]   # legacy single-weekday
        return bool(wd) and d.weekday() in wd
    if kind == "days_of_month":
        dom = tmpl.recurrence_days_of_month
        if dom is None and tmpl.day_of_month is not None:
            dom = [tmpl.day_of_month]
        return bool(dom) and d.day in dom
    if kind == "anniversary":
        a = tmpl.anchor_date
        return a is not None and d.month == a.month and d.day == a.day
    if kind in ("every_n_days", "every_n_months", "every_n_years"):
        a = tmpl.anchor_date
        n = tmpl.interval_n or 1
        if not a or d < a:
            return False
        if kind == "every_n_days":
            return ((d - a).days % n) == 0
        if kind == "every_n_months":
            if d.day != a.day:
                return False
            months = (d.year - a.year) * 12 + (d.month - a.month)
            return months >= 0 and months % n == 0
        if kind == "every_n_years":
            if d.month != a.month or d.day != a.day:
                return False
            years = d.year - a.year
            return years >= 0 and years % n == 0
    return False


def _default_weekend_rule(kind: str) -> str:
    """Daily tasks just skip Sat/Sun. Anything monthly+ rolls forward to
    Monday so a 1st-of-month task still fires when the 1st is a weekend."""
    if kind in ("daily", "weekdays_of_week"):
        return "skip"
    return "roll_to_monday"


def _adjust_for_weekend(tmpl: TaskTemplate, d: date) -> Optional[date]:
    """Apply the weekend rule. Returns the date the instance should be
    created for, or None if the rule says skip."""
    if not _is_weekend(d):
        return d
    kind = _resolve_recurrence_kind(tmpl)
    rule = tmpl.weekend_rule or _default_weekend_rule(kind)
    if rule == "skip":
        return None
    if rule == "roll_to_monday":
        # Sat → +2, Sun → +1
        return d + timedelta(days=(7 - d.weekday()))
    return d


def generate_instances_for_date(db: Session, target_date: date) -> dict:
    """Create TaskInstance rows for every (active template × matching user)
    that should fire on or roll into target_date.

    A template can land on target_date in two ways:
      1. Directly — _template_fires_on(tmpl, target_date) is True and
         target_date isn't suppressed by the weekend rule.
      2. Rolled forward — _template_fires_on fires on the prior Sat/Sun
         and the weekend rule is roll_to_monday.

    Idempotent — uses the unique (template_id, email, due_date) constraint.
    """
    candidates: list[tuple[TaskTemplate, date]] = []
    templates = db.query(TaskTemplate).filter(TaskTemplate.active.is_(True)).all()

    for tmpl in templates:
        # Direct hit on target_date
        if _template_fires_on(tmpl, target_date):
            adj = _adjust_for_weekend(tmpl, target_date)
            if adj == target_date:
                candidates.append((tmpl, target_date))
        # Roll-forward: if target_date is Mon, a task that fired on Sat or
        # Sun with roll_to_monday rolls into today.
        if target_date.weekday() == 0:  # Monday
            for back in (1, 2):  # Sun=−1, Sat=−2
                prior = target_date - timedelta(days=back)
                if _template_fires_on(tmpl, prior):
                    adj = _adjust_for_weekend(tmpl, prior)
                    if adj == target_date:
                        candidates.append((tmpl, target_date))
                        break

    created = 0
    skipped = 0
    for tmpl, d in candidates:
        users = _assignees_for_template(db, tmpl)
        for u in users:
            existing = db.query(TaskInstance).filter(
                TaskInstance.template_id == tmpl.id,
                TaskInstance.assigned_to_email == u.email,
                TaskInstance.due_date == d,
            ).first()
            if existing:
                skipped += 1
                continue
            due_at = None
            if tmpl.due_time:
                due_at = datetime.combine(d, tmpl.due_time)
            db.add(TaskInstance(
                template_id=tmpl.id,
                assigned_to_email=u.email,
                due_date=d,
                due_at=due_at,
                status="pending",
            ))
            created += 1

    db.commit()
    return {"target_date": str(target_date), "created": created, "skipped": skipped}


def my_today(db: Session, email: str, target_date: Optional[date] = None) -> List[dict]:
    """Return today's tasks for one user, ordered by due_at then priority."""
    target_date = target_date or date.today()
    rows = (
        db.query(TaskInstance, TaskTemplate)
        .join(TaskTemplate, TaskInstance.template_id == TaskTemplate.id)
        .filter(
            TaskInstance.assigned_to_email == email,
            TaskInstance.due_date == target_date,
        )
        .order_by(TaskInstance.due_at.asc().nullslast(), TaskTemplate.priority.desc())
        .all()
    )
    out = []
    for inst, tmpl in rows:
        out.append({
            "id": str(inst.id),
            "title": tmpl.title,
            "question_text": tmpl.question_text or tmpl.title,
            "description": tmpl.description,
            "instructions": tmpl.instructions,
            "category": tmpl.category,
            "role": tmpl.role,
            "priority": tmpl.priority,
            "followup_kind": tmpl.followup_kind or "none",
            "followup_prompt": tmpl.followup_prompt,
            "due_at": str(inst.due_at) if inst.due_at else None,
            "status": inst.status,
            "answer": inst.answer,
            "followup_count": inst.followup_count,
            "followup_text": inst.followup_text,
            "completed_at": str(inst.completed_at) if inst.completed_at else None,
            "completed_by": inst.completed_by,
            "skipped_reason": inst.skipped_reason,
            "notes": inst.notes,
        })
    return out


def record_answer(
    db: Session,
    instance_id: str,
    by_email: str,
    answer: str,
    followup_count: Optional[int] = None,
    followup_text: Optional[str] = None,
) -> TaskInstance:
    """Record a Yes/No answer for a task instance.

    Yes  → status=done, no follow-up required.
    No   → status=done (the question was answered), but the follow-up fields
           must be provided per the template's followup_kind. The manager
           dashboard surfaces all No-answers regardless.
    """
    inst = db.query(TaskInstance).filter(TaskInstance.id == instance_id).first()
    if inst is None:
        raise ValueError(f"task instance {instance_id} not found")
    if answer not in ("yes", "no"):
        raise ValueError("answer must be 'yes' or 'no'")

    tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == inst.template_id).first()
    fkind = (tmpl.followup_kind if tmpl else "none") or "none"

    if answer == "no" and fkind != "none":
        if fkind == "count" and followup_count is None:
            raise ValueError("followup_count is required when answering 'no'")
        if fkind == "reason" and not (followup_text and followup_text.strip()):
            raise ValueError("followup_text is required when answering 'no'")

    inst.answer = answer
    inst.followup_count = followup_count if answer == "no" else None
    inst.followup_text = (followup_text or None) if answer == "no" else None
    inst.status = "done"
    inst.completed_at = datetime.utcnow()
    inst.completed_by = by_email
    db.commit(); db.refresh(inst)
    return inst


def mark_skipped(db: Session, instance_id: str, by_email: str, reason: str) -> TaskInstance:
    inst = db.query(TaskInstance).filter(TaskInstance.id == instance_id).first()
    if inst is None:
        raise ValueError(f"task instance {instance_id} not found")
    inst.status = "skipped"
    inst.completed_at = datetime.utcnow()
    inst.completed_by = by_email
    inst.skipped_reason = reason
    db.commit(); db.refresh(inst)
    return inst


def reopen(db: Session, instance_id: str) -> TaskInstance:
    """Un-mark a done/skipped task back to pending."""
    inst = db.query(TaskInstance).filter(TaskInstance.id == instance_id).first()
    if inst is None:
        raise ValueError(f"task instance {instance_id} not found")
    inst.status = "pending"
    inst.completed_at = None
    inst.completed_by = None
    inst.skipped_reason = None
    inst.answer = None
    inst.followup_count = None
    inst.followup_text = None
    db.commit(); db.refresh(inst)
    return inst
