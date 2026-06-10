"""HIPAA-compliant audit logging service."""

from datetime import datetime
from app.utils.dt import now_utc_naive
from typing import Any, Dict, Optional, Union

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


# Sentinel for non-user actors (Cloud Scheduler jobs, fax-poller, etc.).
# Pass `actor="system"` rather than leaving the actor blank — silence on
# a PHI access is a HIPAA gap, but a labelled system actor is fine.
ACTOR_SYSTEM = "system"


def _derive_actor(actor: Union[Dict, str, None]) -> tuple[Optional[str], Optional[str]]:
    """Extract (user_id, user_name) from the canonical actor argument.

    Accepts:
      - dict: a get_current_user payload — uses 'email' for user_id and
              'name' (falling back to email) for user_name.
      - str:  treated as the user_id (typically an email) and used as
              user_name too. ACTOR_SYSTEM ("system") is the conventional
              value for background workers.
      - None: returns (None, None) — caller will be rejected by the
              required-actor check unless they passed user_id/user_name
              explicitly.
    """
    if actor is None:
        return None, None
    if isinstance(actor, dict):
        email = (actor.get("email") or "").lower().strip() or None
        name = actor.get("name") or email
        return email, name
    s = str(actor).strip()
    if not s:
        return None, None
    return s.lower() if s != ACTOR_SYSTEM else s, s


def log_action(
    db: Session,
    action: str,
    resource_type: str,
    *,
    actor: Union[Dict, str, None] = None,
    resource_id: Optional[str] = None,
    patient_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    ip_address: Optional[str] = None,
    description: Optional[str] = None,
    old_values: Optional[Dict] = None,
    new_values: Optional[Dict] = None,
    status: str = "success",
    error_detail: Optional[str] = None,
    defer_commit: bool = False,
) -> AuditLog:
    """Record an action in the HIPAA audit log.

    Identity:
      Pass `actor=current_user` (the get_current_user dict), or
      `actor="some-email@waldorf"`, or `actor=ACTOR_SYSTEM` for
      background work. The helper derives `user_id` and `user_name`
      from that single argument. Legacy callsites that explicitly
      pass `user_id=` and/or `user_name=` are still accepted.

      Calls with NO actor of any kind raise ValueError — a silent
      missing actor on a PHI access is a HIPAA gap. (Fable design
      review note 3.)

    Transaction:
      By default (`defer_commit=False`), this helper commits the audit
      row in its own transaction immediately. That preserves the
      "audit always lands" behavior the older codebase assumes.

      Pass `defer_commit=True` to instead just flush the row into the
      caller's session — the audit row will land atomically with the
      caller's next `db.commit()`, and roll back together if the
      caller raises. Use this for any action where the audit row is
      meaningless without the business write also succeeding (e.g.,
      `log "Hard-deleted X"` before actually deleting X). New code
      should prefer the deferred form. (Fable design review note 4.)
    """
    actor_uid, actor_uname = _derive_actor(actor)
    user_id = user_id or actor_uid
    user_name = user_name or actor_uname or user_id
    if not user_id and not user_name:
        raise ValueError(
            f"log_action({action!r}) requires an actor — pass "
            f"actor=current_user, actor='email', or actor=ACTOR_SYSTEM "
            "(or supply user_id / user_name explicitly). Silent audit "
            "entries are not allowed."
        )

    entry = AuditLog(
        timestamp=now_utc_naive(),
        user_id=user_id,
        user_name=user_name,
        ip_address=ip_address,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        patient_id=str(patient_id) if patient_id else None,
        description=description,
        old_values=old_values,
        new_values=new_values,
        status=status,
        error_detail=error_detail,
    )
    db.add(entry)
    if defer_commit:
        db.flush()  # caller's commit makes the row durable
    else:
        db.commit()
        db.refresh(entry)
    return entry


def log_view(
    db: Session,
    resource_type: str,
    resource_id: str,
    current_user: Optional[Dict] = None,
    patient_id: Optional[str] = None,
    description: Optional[str] = None,
) -> AuditLog:
    """Record a read-event in the audit log (HIPAA "who saw what" trail).

    Differs from log_action only in its calling shape — pulls user identity
    out of the get_current_user dict for you and uses action='VIEW'.
    """
    return log_action(
        db,
        action="VIEW",
        resource_type=resource_type,
        actor=current_user,
        resource_id=resource_id,
        patient_id=patient_id,
        description=description,
    )
