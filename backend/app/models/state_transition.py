"""Cross-module state-transition audit table.

Captures one immutable row per state change for any entity in the system.
The existing per-module audit tables (PelletAuditEvent, LarcAuditEvent,
ActiveClaimNote, SurgeryNote) each have their own conventions and
sometimes mix audit with user notes. This table is the single source of
truth for "who flipped this entity from A to B and when".

Write-only by design — no DELETE endpoint exposed. Compliance/forensics
querying happens through the audit dashboards.
"""
from sqlalchemy import Column, DateTime, Index, JSON, String, Text
from datetime import datetime
from app.utils.dt import now_utc_naive

from app.database import Base
from app.models.guid import GUID, new_uuid


class StateTransitionAudit(Base):
    __tablename__ = "state_transition_audit"
    __table_args__ = (
        Index("ix_state_transition_entity",  "entity_type", "entity_id"),
        Index("ix_state_transition_actor",   "actor", "at"),
        Index("ix_state_transition_at",      "at"),
    )

    id           = Column(GUID(), primary_key=True, default=new_uuid)
    entity_type  = Column(String(40),  nullable=False)
    # e.g. 'surgery', 'surgery_milestone', 'larc_assignment', 'larc_device',
    #      'pellet_visit', 'active_claim', 'block_day'
    entity_id    = Column(String(40),  nullable=False)
    action       = Column(String(60),  nullable=False)
    # e.g. 'status_changed', 'milestone_advanced', 'cancelled', 'rescheduled'
    before_value = Column(String(120), nullable=True)
    after_value  = Column(String(120), nullable=True)
    actor        = Column(String(120), nullable=False)
    at           = Column(DateTime,    default=now_utc_naive, nullable=False)
    detail       = Column(JSON,        nullable=True)
    summary      = Column(Text,        nullable=True)
