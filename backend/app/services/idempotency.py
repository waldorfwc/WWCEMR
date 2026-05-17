"""Idempotency-key middleware-style helper.

Usage in a route:

    @router.post("/era/commit")
    def commit_era(
        ...,
        idem: IdempotencyChecker = Depends(idempotency_for("POST /era/commit")),
    ):
        if idem.cached:
            return idem.cached       # 200/4xx response from a prior call
        result = do_the_work(...)
        idem.store(result, status_code=200)
        return result

The dependency reads the `Idempotency-Key` header. If absent the request
runs normally without caching (idempotency is opt-in per call). If
present and a cached entry exists, the cached body is returned directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.idempotency import IdempotencyKey


CACHE_TTL_HOURS = 24


class IdempotencyChecker:
    """A per-request handle. Used by route bodies to short-circuit on a
    cached prior response or to store the new one for future retries."""

    def __init__(self, db: Session, actor: str, route: str, key: Optional[str]):
        self.db = db
        self.actor = actor
        self.route = route
        self.key = key
        self.cached = None
        if key:
            existing = (db.query(IdempotencyKey)
                          .filter(IdempotencyKey.actor == actor,
                                  IdempotencyKey.route == route,
                                  IdempotencyKey.key == key)
                          .first())
            if existing:
                # Honor TTL: stale rows act as if missing.
                if (datetime.utcnow() - existing.created_at
                       <= timedelta(hours=CACHE_TTL_HOURS)):
                    try:
                        self.cached = json.loads(existing.response_body)
                    except Exception:
                        self.cached = None

    def store(self, body: Any, status_code: int = 200) -> None:
        if not self.key:
            return
        try:
            serialized = json.dumps(body, default=str)
        except Exception:
            return
        # Upsert: another concurrent call may have just landed.
        existing = (self.db.query(IdempotencyKey)
                      .filter(IdempotencyKey.actor == self.actor,
                              IdempotencyKey.route == self.route,
                              IdempotencyKey.key == self.key).first())
        if existing is None:
            self.db.add(IdempotencyKey(
                actor=self.actor, route=self.route, key=self.key,
                status_code=status_code, response_body=serialized,
            ))


def idempotency_for(route_name: str):
    """Factory that returns a FastAPI dependency.

    `route_name` is a short human-readable identifier ('POST /era/commit')
    — it scopes the cache so the same Idempotency-Key value can be reused
    on different endpoints without collision."""
    def _dep(
        request: Request,
        db: Session = Depends(get_db),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> IdempotencyChecker:
        # Resolve current user without hard-failing — the route's own
        # require_permission handles auth; we just need an actor string.
        actor = ""
        try:
            from app.routers.auth import get_current_user
            user = get_current_user(request)   # type: ignore[arg-type]
            actor = (user.get("email") if isinstance(user, dict) else "") or ""
        except Exception:
            actor = "anonymous"
        return IdempotencyChecker(db=db, actor=actor or "anonymous",
                                    route=route_name,
                                    key=(idempotency_key or "").strip() or None)
    return _dep


def sweep_stale_idempotency_keys(db: Session, ttl_hours: int = CACHE_TTL_HOURS) -> int:
    """Delete rows older than ttl_hours. Run from the nightly scheduler."""
    cutoff = datetime.utcnow() - timedelta(hours=ttl_hours)
    n = (db.query(IdempotencyKey)
           .filter(IdempotencyKey.created_at < cutoff)
           .delete(synchronize_session=False))
    if n:
        db.commit()
    return n
