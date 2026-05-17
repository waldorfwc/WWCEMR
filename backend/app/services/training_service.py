"""Training & certification service.

Three primitives:

  is_trainer(user, template)    — is this user authorized to certify others?
  is_certified(user, template)  — does this user have an active, unexpired cert?
  certified_emails_for(template)— who can be assigned this template today?

Plus the state-transition helpers:

  authorize_trainer(...)        — manager grants trainer rights
  revoke_trainer(...)
  certify(trainer, trainee, …)  — trainer signs; cert in 'pending_trainee'
  acknowledge(trainee, …)       — trainee confirms → 'active' (or 'disputed')
  revoke_cert(...)              — manager pulls a cert
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Set

from sqlalchemy.orm import Session

from app.models.checklist import TaskTemplate
from app.models.training import TrainerAuthorization, TrainingCertification


# ─── Helpers ─────────────────────────────────────────────────────────

def compute_expires_on(tmpl: TaskTemplate, signed_at: datetime) -> Optional[date]:
    """When does a cert signed at `signed_at` expire?

    Returns None for 'never'. For relative kinds, computes the date
    `expires_value` units past sign date. For 'specific_date', returns
    `expires_on_date` directly (everyone's cert lapses on that day).
    """
    kind = (tmpl.expires_kind or "never").lower()
    if kind == "never":
        return None

    base = signed_at.date()
    n = tmpl.expires_value or 0

    if kind == "days":
        return base + timedelta(days=n)
    if kind == "weeks":
        return base + timedelta(weeks=n)
    if kind == "months":
        # Calendar-aware month math: same day-of-month n months later.
        # Falls back to last day of target month if the original day
        # doesn't exist (e.g. Jan 31 + 1mo → Feb 28).
        from calendar import monthrange
        y, m = base.year, base.month + n
        while m > 12:
            y += 1; m -= 12
        last = monthrange(y, m)[1]
        return date(y, m, min(base.day, last))
    if kind == "years":
        from calendar import monthrange
        y = base.year + n
        last = monthrange(y, base.month)[1]
        return date(y, base.month, min(base.day, last))
    if kind == "specific_date":
        return tmpl.expires_on_date
    return None


def _is_active_cert(c: TrainingCertification, today: Optional[date] = None) -> bool:
    if c.status != "active":
        return False
    if c.revoked_at is not None:
        return False
    if c.expires_on is not None:
        if (today or date.today()) > c.expires_on:
            return False
    return True


def is_certified(db: Session, user_email: str, template_id) -> bool:
    """Is this user actively certified on this template right now?"""
    c = (db.query(TrainingCertification)
           .filter(TrainingCertification.user_email == user_email,
                   TrainingCertification.template_id == template_id)
           .first())
    return bool(c and _is_active_cert(c))


def is_trainer(db: Session, user_email: str, template_id) -> bool:
    """Is this user an authorized trainer for this template?
    Admins (system:admin) are NOT auto-trainers — that's an authorization
    decision the manager has to make per task. Caller code may bypass.
    """
    a = (db.query(TrainerAuthorization)
           .filter(TrainerAuthorization.user_email == user_email,
                   TrainerAuthorization.template_id == template_id,
                   TrainerAuthorization.revoked_at.is_(None))
           .first())
    return a is not None


def certified_emails_for(db: Session, template_id) -> Set[str]:
    """All users with an active (unexpired) cert on this template."""
    today = date.today()
    rows = (db.query(TrainingCertification)
              .filter(TrainingCertification.template_id == template_id,
                      TrainingCertification.status == "active",
                      TrainingCertification.revoked_at.is_(None))
              .all())
    return {c.user_email for c in rows
            if c.expires_on is None or c.expires_on >= today}


# ─── State transitions ──────────────────────────────────────────────

def authorize_trainer(
    db: Session, *,
    user_email: str, template_id,
    authorized_by: str, notes: Optional[str] = None,
) -> TrainerAuthorization:
    """Manager grants trainer authority. Idempotent — if a non-revoked
    row already exists, returns it. If a revoked row exists, un-revokes."""
    user_email = (user_email or "").lower().strip()
    existing = (db.query(TrainerAuthorization)
                  .filter(TrainerAuthorization.user_email == user_email,
                          TrainerAuthorization.template_id == template_id)
                  .first())
    if existing:
        if existing.revoked_at is not None:
            existing.revoked_at = None
            existing.revoked_by = None
            existing.revoked_reason = None
            existing.authorized_by = authorized_by
            existing.authorized_at = datetime.utcnow()
            if notes:
                existing.notes = notes
            db.commit(); db.refresh(existing)
        return existing

    row = TrainerAuthorization(
        user_email=user_email,
        template_id=template_id,
        authorized_by=authorized_by,
        notes=notes,
    )
    db.add(row); db.commit(); db.refresh(row)
    return row


def revoke_trainer(
    db: Session, *,
    user_email: str, template_id,
    revoked_by: str, reason: Optional[str] = None,
) -> TrainerAuthorization:
    user_email = (user_email or "").lower().strip()
    row = (db.query(TrainerAuthorization)
             .filter(TrainerAuthorization.user_email == user_email,
                     TrainerAuthorization.template_id == template_id)
             .first())
    if row is None:
        raise ValueError("trainer authorization not found")
    if row.revoked_at is not None:
        return row
    row.revoked_at = datetime.utcnow()
    row.revoked_by = revoked_by
    row.revoked_reason = reason
    db.commit(); db.refresh(row)
    return row


def certify(
    db: Session, *,
    trainer_email: str, trainee_email: str, template_id,
    notes: Optional[str] = None, bypass_trainer_check: bool = False,
) -> TrainingCertification:
    """Trainer signs that they trained the trainee.

    Requires the trainer to have a non-revoked TrainerAuthorization for
    this template — UNLESS bypass_trainer_check is True, which the router
    passes when the caller has `training:authorize` (super users / office
    managers). This lets a super user mark anyone trained without first
    self-authorizing as a per-task trainer.

    Creates a TrainingCertification in 'pending_trainee' status — it
    isn't valid until the trainee acknowledges.
    """
    trainer_email = (trainer_email or "").lower().strip()
    trainee_email = (trainee_email or "").lower().strip()
    if trainer_email == trainee_email:
        raise ValueError("trainer cannot certify themselves")
    if not bypass_trainer_check and not is_trainer(db, trainer_email, template_id):
        raise ValueError(f"{trainer_email} is not an authorized trainer for this template")

    # Idempotency-ish: if a cert exists and isn't revoked, replace its
    # trainer signature (re-cert flow) rather than creating a duplicate.
    existing = (db.query(TrainingCertification)
                  .filter(TrainingCertification.user_email == trainee_email,
                          TrainingCertification.template_id == template_id)
                  .first())
    if existing:
        existing.trainer_email = trainer_email
        existing.trainer_signed_at = datetime.utcnow()
        existing.trainee_signed_at = None
        existing.status = "pending_trainee"
        existing.expires_on = None
        existing.revoked_at = None
        existing.revoked_by = None
        existing.revoked_reason = None
        if notes:
            existing.notes = notes
        db.commit(); db.refresh(existing)
        return existing

    row = TrainingCertification(
        user_email=trainee_email,
        template_id=template_id,
        trainer_email=trainer_email,
        trainer_signed_at=datetime.utcnow(),
        status="pending_trainee",
        notes=notes,
    )
    db.add(row); db.commit(); db.refresh(row)
    return row


def acknowledge(
    db: Session, *,
    cert_id, trainee_email: str, confirm: bool,
    dispute_reason: Optional[str] = None,
) -> TrainingCertification:
    """Trainee responds to the trainer's certification.

    confirm=True  → status='active', compute expires_on from the template.
    confirm=False → status='disputed' (manager can resolve / revoke).
    """
    trainee_email = (trainee_email or "").lower().strip()
    row = db.query(TrainingCertification).filter(TrainingCertification.id == cert_id).first()
    if row is None:
        raise ValueError("certification not found")
    if row.user_email != trainee_email:
        raise ValueError("you can only acknowledge your own certification")
    if row.status != "pending_trainee":
        raise ValueError(f"certification is not pending — current status {row.status}")

    row.trainee_signed_at = datetime.utcnow()
    if confirm:
        row.status = "active"
        tmpl = db.query(TaskTemplate).filter(TaskTemplate.id == row.template_id).first()
        if tmpl:
            row.expires_on = compute_expires_on(tmpl, row.trainee_signed_at)
    else:
        row.status = "disputed"
        if dispute_reason:
            row.notes = ((row.notes or "") + f"\n[disputed] {dispute_reason}").strip()
    db.commit(); db.refresh(row)
    return row


def revoke_cert(
    db: Session, *,
    cert_id, revoked_by: str, reason: Optional[str] = None,
) -> TrainingCertification:
    row = db.query(TrainingCertification).filter(TrainingCertification.id == cert_id).first()
    if row is None:
        raise ValueError("certification not found")
    row.status = "revoked"
    row.revoked_at = datetime.utcnow()
    row.revoked_by = revoked_by
    row.revoked_reason = reason
    db.commit(); db.refresh(row)
    return row
