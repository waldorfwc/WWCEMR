"""Shared model mixins. Currently: SoftDeleteMixin.

Per Fable's design review note 13: bank_recon.delete_import hard-
deletes a Bai2Import row even though the cascade takes its dedup_keys
with it, which means the same transactions can be re-imported and
re-posted later. The code apologizes for its own footgun in the audit
description. Soft-delete on financially-significant tables fixes the
semantics: the row stays, queries filter it out by default, and an
"undelete" / "trash" admin path exists when someone clicks the wrong
import.

Usage:

    class Bai2Import(Base, SoftDeleteMixin):
        ...

Default queries do NOT automatically hide soft-deleted rows — that's
intentional. Callers explicitly opt in by chaining `.not_deleted()`,
which avoids surprising old queries that need to count or report on
deleted rows (e.g. audit log of "how many things have we deleted").

    rows = (db.query(Bai2Import)
              .filter(Bai2Import.not_deleted())
              .order_by(Bai2Import.generated_at.desc())
              .all())

`row.soft_delete(by_email)` marks the row deleted but keeps the data,
and `row.restore()` brings it back. Both leave timestamps for audit.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Column, DateTime, String

from app.utils.dt import now_utc_naive


class SoftDeleteMixin:
    """Add `deleted_at` + `deleted_by` columns and helpers."""

    deleted_at = Column(DateTime, nullable=True, index=True)
    deleted_by = Column(String(200), nullable=True)

    @classmethod
    def not_deleted(cls):
        """Use in `.filter(...)` clauses to hide soft-deleted rows."""
        return cls.deleted_at.is_(None)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, by_email: Optional[str] = None) -> None:
        self.deleted_at = now_utc_naive()
        self.deleted_by = by_email

    def restore(self) -> None:
        self.deleted_at = None
        self.deleted_by = None
