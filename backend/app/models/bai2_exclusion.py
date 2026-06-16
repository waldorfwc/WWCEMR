"""Sticky per-transaction bank-recon exclusions.

When a user excludes an otherwise-importable transaction at /generate, we
remember it by identity (date + amount + last_4) so future uploads of the
same bank transaction are auto-excluded ("previously excluded"). A manager
can reinstate (un-stick) one via the admin list so it can import again.

Soft-delete = reinstated: `deleted_at`/`deleted_by` mark a reinstatement,
not a hard removal, so the audit trail (who excluded, who reinstated) is
preserved and a re-exclude reactivates the same row.
"""
from __future__ import annotations

from sqlalchemy import Column, String, Date, DateTime, Numeric, Text

from app.database import Base
from app.models.guid import GUID, new_uuid
from app.models.mixins import SoftDeleteMixin
from app.utils.dt import now_utc_naive


class Bai2Exclusion(Base, SoftDeleteMixin):
    """One sticky exclusion keyed by transaction identity.

    `exclusion_key` = sha256(f"{date}|{_q2(amount)}|{last_4 or ''}") and is
    unique, so re-excluding the same identity reactivates the existing row
    rather than inserting a duplicate. `deleted_at IS NULL` == active
    (still enforced); a soft-deleted row == reinstated.
    """
    __tablename__ = "bai2_exclusions"

    id = Column(GUID(), primary_key=True, default=new_uuid)
    exclusion_key = Column(String(64), nullable=False, unique=True)  # sha256(date|amount|last4)
    transaction_date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    last_4 = Column(String(4), nullable=True)
    description = Column(Text, nullable=True)         # formatted_text/desc for display
    reason = Column(Text, nullable=True)
    excluded_by = Column(String(200), nullable=True)
    source_import_id = Column(GUID(), nullable=True)  # import where it was first excluded
    created_at = Column(DateTime, default=now_utc_naive, nullable=False)
    # SoftDeleteMixin adds: deleted_at (= reinstated_at), deleted_by (= reinstated_by)
