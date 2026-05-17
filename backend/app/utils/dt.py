"""Datetime helpers — single source of truth for "now" in the codebase.

Python 3.12 deprecated `datetime.utcnow()` in favor of `datetime.now(tz=UTC)`.
We're still on 3.9, but cleanup is cheap: use `now_utc()` for new code, and
migrate existing call sites opportunistically.

Two functions:
  • now_utc()        → timezone-aware UTC datetime (preferred)
  • now_utc_naive()  → timezone-NAIVE UTC datetime (back-compat for columns
                        that store naive timestamps in SQLite, which most of
                        ours do — the DB returns naive on read)

When persisting to a `DateTime` column that's been storing naive values,
keep using `now_utc_naive()` to avoid `can't subtract offset-naive and
offset-aware datetimes` errors on comparisons.
"""
from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    """Timezone-aware UTC. Use for: token expiry, log timestamps, anything
    compared against another aware datetime."""
    return datetime.now(timezone.utc)


def now_utc_naive() -> datetime:
    """Timezone-NAIVE UTC. Use for: writing to existing `DateTime` columns
    that have always stored naive UTC (most of the codebase). Equivalent
    to the old `datetime.utcnow()` but without the deprecation warning in
    Python 3.12+."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
