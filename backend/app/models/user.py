"""User model.

The legacy `group` enum (admin/billing/clinical) and `practice_role` string
are still here for backwards compatibility while the RBAC migration is in
progress. The new source of truth is `User.groups` (M2M to the Group table)
plus `permissions_extra` / `permissions_revoked`.
"""
from sqlalchemy import Column, String, DateTime, Boolean, JSON, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base
from app.models.groups import user_groups  # ensure the Table is in metadata before mapper config


class UserGroup(str, enum.Enum):
    ADMIN = "admin"
    BILLING = "billing"
    CLINICAL = "clinical"


# Practice-role values used by the checklist module. String not enum so
# admins can extend without a migration.
# DEPRECATED — being replaced by Group memberships. Kept until Phase 4.
PRACTICE_ROLES = (
    "ma",                  # Medical Assistant
    "front_desk",          # Front Desk Receptionist
    "billing_coding",
    "billing_payments",
    "billing_denials",
    "caribcall",           # CaribCall / Virtual Receptionist
    "office_manager",
    "provider",
)


class User(Base):
    __tablename__ = "users"

    email = Column(String(200), primary_key=True)
    group = Column(SAEnum(UserGroup), default=UserGroup.CLINICAL, nullable=False, index=True)
    display_name = Column(String(200), nullable=True)

    # Checklist fields (populated via lightweight migration)
    practice_role = Column(String(40), nullable=True)
    phone_number = Column(String(20), nullable=True)
    slack_user_id = Column(String(50), nullable=True)
    notify_email = Column(Boolean, default=True)
    notify_slack = Column(Boolean, default=True)
    notify_sms = Column(Boolean, default=False)

    # RBAC overrides (Phase 1). JSON list of permission strings.
    # Effective permissions = union(group perms) | extras − revoked.
    permissions_extra = Column(JSON, nullable=True)
    permissions_revoked = Column(JSON, nullable=True)

    # RingCentral identity for click-to-dial. user_id is the platform-level
    # ID; extension is the human-readable extension number; callback_number
    # is the actual PSTN phone RC dials first (must be different from the
    # number being called).
    ringcentral_user_id = Column(String(40), nullable=True)
    ringcentral_extension = Column(String(20), nullable=True)
    ringcentral_callback_number = Column(String(30), nullable=True)
    # When True, the RC fields above were set by hand (not by the
    # email-matching auto-sync) and the auto-sync must leave them alone.
    # Use cases: a WWC user whose RC seat is registered under a different
    # email, or two WWC accounts sharing one RC seat.
    ringcentral_manual_override = Column(Boolean, default=False, nullable=False)

    # Google Workspace lifecycle (Phase 7).
    # is_active=False → login refused, user excluded from task generation
    # and pickers, but the row is preserved for audit history.
    # Flipped automatically by the Google sync; admins may also toggle.
    is_active = Column(Boolean, default=True, nullable=False)
    # True when this row was created by the Google sync (vs. admin-created).
    auto_provisioned = Column(Boolean, default=False, nullable=False)
    # Timestamp of the last successful sync that included this user.
    last_google_sync = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Many-to-many to Group via user_groups join table
    groups = relationship(
        "Group",
        secondary=user_groups,
        back_populates="members",
        lazy="selectin",
    )
