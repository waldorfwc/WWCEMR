"""Idempotency-key middleware-style helper.

# Convention (Fable design review note 12)

The codebase has three idempotency mechanisms; each addresses a
different shape of "don't do this twice." Pick by the problem, not by
familiarity:

  1. **IdempotencyChecker (this module)** — generic Idempotency-Key
     HTTP header cache, keyed on (actor, route, header). Use when a
     client retries a POST after a network blip and the response is
     expensive/destructive. Per-call opt-in; no header = no caching.

  2. **Per-row client_request_id columns** (e.g. fax_logs) — when the
     domain entity itself has a natural idempotency key the database
     should enforce, like a "send fax" intent that maps 1:1 to a
     FaxLog row. The unique partial index is the durable guarantee.

  3. **Postgres advisory locks** (bank_recon preview→generate) —
     when two requests would race on the SAME computed resource
     (preview_id) and need to be serialized but neither is a "retry."
     Not idempotency — mutual exclusion.

If you're tempted to add a fourth: it's almost certainly one of these.

# Usage

Simple form (handler shape):

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

Sugar form for routes whose body is one function call:

    @router.post("/era/commit")
    def commit_era(..., idem=Depends(idempotency_for("POST /era/commit"))):
        return idem.run(lambda: do_the_work(...))

The dependency reads the `Idempotency-Key` header. If absent the request
runs normally without caching (idempotency is opt-in per call). If
present and a cached entry exists, the cached body is returned directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from app.utils.dt import now_utc_naive
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
                if (now_utc_naive() - existing.created_at
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

    def run(self, fn, status_code: int = 200):
        """Sugar wrapper: short-circuit on cached, else run + store.

        Usage:
            return idem.run(lambda: do_the_work(...))

        Without an Idempotency-Key header, behaves as if not cached —
        runs `fn` and returns the result without caching.
        """
        if self.cached is not None:
            return self.cached
        result = fn()
        self.store(result, status_code=status_code)
        return result


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
    cutoff = now_utc_naive() - timedelta(hours=ttl_hours)
    n = (db.query(IdempotencyKey)
           .filter(IdempotencyKey.created_at < cutoff)
           .delete(synchronize_session=False))
    if n:
        db.commit()
    return n
