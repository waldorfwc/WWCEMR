"""Global soft-delete query filter for Surgery.

Registers a SQLAlchemy ``do_orm_execute`` event that injects a
``with_loader_criteria(Surgery, deleted_at IS NULL)`` into every ORM SELECT,
so soft-deleted surgeries disappear from the ~113 ``db.query(Surgery)`` sites
across routers/services without editing each call site.

Opt out of the filter on a per-statement basis by chaining
``.execution_options(include_deleted=True)`` (used by the restore endpoint and
any admin/audit view that needs to see deleted rows).

Column loads and relationship lazy-loads are excluded so the filter doesn't
interfere with attribute refreshes or relationship traversal.

Importing this module has the side effect of registering the listener; it is
imported once from ``app.main`` at startup.
"""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session as _Session, with_loader_criteria

from app.models.surgery import Surgery


@event.listens_for(_Session, "do_orm_execute")
def _filter_soft_deleted_surgery(execute_state):
    if (
        execute_state.is_select
        and not execute_state.is_column_load
        and not execute_state.is_relationship_load
        and not execute_state.execution_options.get("include_deleted", False)
    ):
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                Surgery,
                lambda cls: cls.deleted_at.is_(None),
                include_aliases=True,
            )
        )
