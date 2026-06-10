"""Pellet inventory lock — a single practice-wide kill switch that
prevents admin-style inventory edits (lot metadata, dose-type catalog,
historical visit fix-ups) without affecting normal workflow.

Stored in PracticeConfig under key `pellet_inventory_lock`. Value is a
JSON blob: {locked, locked_at, locked_by, reason}.

Normal flow (visits, bagging, returning unused doses, transfers, counts)
is NOT blocked by this — those run through the audited dose pipeline and
remain available post-lock so the practice can keep operating.

A `pellet:manage` admin may override the lock per-call by passing an
`override_reason` query parameter on the guarded endpoint; the override
is audited."""
from __future__ import annotations

import json
from datetime import datetime
from app.utils.dt import now_utc_naive
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.practice_config import PracticeConfig
from app.services.audit_service import log_action

LOCK_KEY = "pellet_inventory_lock"


def get_lock_state(db: Session) -> dict:
    row = db.query(PracticeConfig).filter(PracticeConfig.key == LOCK_KEY).first()
    if not row or not row.value:
        return {"locked": False, "locked_at": None,
                "locked_by": None, "reason": None}
    try:
        data = json.loads(row.value)
    except (ValueError, TypeError):
        return {"locked": False, "locked_at": None,
                "locked_by": None, "reason": None}
    return {
        "locked":    bool(data.get("locked")),
        "locked_at": data.get("locked_at"),
        "locked_by": data.get("locked_by"),
        "reason":    data.get("reason"),
    }


def set_lock_state(db: Session, *, locked: bool, by_email: str,
                    reason: Optional[str] = None) -> dict:
    payload = {
        "locked":    bool(locked),
        "locked_at": now_utc_naive().isoformat() if locked else None,
        "locked_by": by_email if locked else None,
        "reason":    (reason or "").strip() or None,
    }
    row = db.query(PracticeConfig).filter(PracticeConfig.key == LOCK_KEY).first()
    if row:
        row.value = json.dumps(payload)
    else:
        db.add(PracticeConfig(key=LOCK_KEY, value=json.dumps(payload)))
    log_action(db, "PELLET_INVENTORY_LOCK_SET", "pellet",
                user_name=by_email,
                description=f"{'LOCKED' if locked else 'UNLOCKED'}"
                            + (f" — {reason}" if reason else ""),
                new_values=payload)
    db.commit()
    return payload


def ensure_unlocked_or_override(db: Session, *,
                                  current_user: dict,
                                  override_reason: Optional[str] = None,
                                  action_label: str = "inventory edit") -> None:
    """Raise 423 (Locked) if the inventory is locked and no override is
    provided. Admins with `pellet:manage` may override by supplying a
    non-empty override_reason; the override is audited."""
    state = get_lock_state(db)
    if not state["locked"]:
        return

    # Pellets:Manage (tier 30) can override the inventory lock. Resolved
    # by direct query so we don't depend on a dict injection that only
    # fires on specific Depends() shapes. (Fable design review note 7.)
    from app.permissions.catalog import Module, Tier
    from app.permissions.resolver import effective_tier
    email = (current_user.get("email") or "").lower().strip()
    is_admin = effective_tier(db, email, Module.PELLETS) >= Tier.MANAGE
    override_reason = (override_reason or "").strip()

    if is_admin and override_reason:
        log_action(db, "PELLET_LOCK_OVERRIDE", "pellet",
                    user_name=current_user.get("email"),
                    description=f"Lock override for {action_label}: {override_reason}")
        db.commit()
        return

    raise HTTPException(
        status_code=423,
        detail=(
            "Pellet inventory is locked. Reason: "
            f"{state.get('reason') or '(none provided)'}. "
            "Admins may override by passing override_reason."
        ),
    )
