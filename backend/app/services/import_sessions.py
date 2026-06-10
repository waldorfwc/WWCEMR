"""In-memory session store for two-step import flows.

Holds the parsed payload between the upload endpoint (which computes the
preview) and the commit endpoint (which persists it).

LIMITATION: This is a module-level dict. Safe for single-process uvicorn,
NOT safe across multiple workers — each worker would have its own dict and
a commit hitting the wrong worker would 404. If the app ever runs multi-
worker, swap this for Redis with the same interface. TODO.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Module-level lock for atomic claim-and-purge so two concurrent
# commits can't both pass get() before purge() and double-post.
# (Fable billing audit H5.)
_lock = threading.Lock()


@dataclass
class SessionEntry:
    session_id: str
    payload: Any                 # the parser's ChargeAnalysisImport result
    filename: str
    file_path: str
    user_email: Optional[str]
    created_at: datetime
    expires_at: datetime
    # Pre-computed per-claim flags for fast commit:
    # list of {visit_id, exists_in_db, patient_resolved_id, will_create_patient}
    claim_flags: List[Dict[str, Any]] = field(default_factory=list)
    # Free-form scratch storage (e.g. drift fingerprints, period dates) that
    # the upload endpoint stashes for the commit endpoint to consume.
    aux: Dict[str, Any] = field(default_factory=dict)


_sessions: Dict[str, SessionEntry] = {}


def put(entry: SessionEntry) -> None:
    _sessions[entry.session_id] = entry


def get(session_id: str) -> Optional[SessionEntry]:
    entry = _sessions.get(session_id)
    if entry is None:
        return None
    if datetime.now(timezone.utc) >= entry.expires_at:
        _sessions.pop(session_id, None)
        return None
    return entry


def purge(session_id: str) -> None:
    _sessions.pop(session_id, None)


def claim(session_id: str) -> Optional[SessionEntry]:
    """Atomic get-and-remove. Two concurrent commits used to both pass
    get() before either reached purge(), so each ran the full post
    loop against the same parsed payload. claim() removes the entry
    under a lock and returns it (or None if it was already claimed,
    expired, or never existed). (Fable billing audit H5.)
    """
    with _lock:
        entry = _sessions.pop(session_id, None)
        if entry is None:
            return None
        if datetime.now(timezone.utc) >= entry.expires_at:
            return None
        return entry


def set_aux(session_id: str, key: str, value: Any) -> None:
    entry = _sessions.get(session_id)
    if entry is not None:
        entry.aux[key] = value


def get_aux(session_id: str, key: str, default: Any = None) -> Any:
    entry = _sessions.get(session_id)
    if entry is None:
        return default
    return entry.aux.get(key, default)


def expire_old() -> int:
    """Drop all expired entries. Returns count removed. Called opportunistically."""
    now = datetime.now(timezone.utc)
    stale = [sid for sid, e in _sessions.items() if now >= e.expires_at]
    for sid in stale:
        _sessions.pop(sid, None)
    return len(stale)
