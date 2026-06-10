"""Idempotency-key cache.

Lets a client safely retry a mutating POST by sending an `Idempotency-Key`
header. The first call processes normally and the response is cached;
subsequent calls with the same (user, route, key) tuple return the
cached response without re-running the handler.

Retention is short — 24h is plenty for "user double-clicked the button"
or "network flaked mid-commit". A cleanup sweep deletes stale rows.
"""
from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from datetime import datetime
from app.utils.dt import now_utc_naive

from app.database import Base
from app.models.guid import GUID, new_uuid


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("actor", "route", "key", name="uq_idempotency_actor_route_key"),
        Index("ix_idempotency_created", "created_at"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    actor        = Column(String(120), nullable=False)
    route        = Column(String(200), nullable=False)   # METHOD + path template
    key          = Column(String(120), nullable=False)   # client-supplied header value
    status_code  = Column(Integer, nullable=False)
    response_body = Column(Text, nullable=False)         # JSON-encoded
    created_at   = Column(DateTime, default=now_utc_naive, nullable=False)
